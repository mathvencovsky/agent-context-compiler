"""Integration tests for POST /v1/documents/resolve — the M5 cache state machine.

HTTP is ALWAYS mocked through the instrumented ``ScriptedTransport`` (see
``conftest.py``); no test touches the internet. Postgres and S3/SeaweedFS are
real (compose). Each test asserts both the HTTP envelope and the persisted
state (version rows, current_version_id) and, where it matters, the transport
call counter — the proof that a cache hit performs zero network I/O.
"""

from __future__ import annotations

import hashlib

import httpx
import pytest
from sqlalchemy import func, select

from app.extract.ir import make_document_id, make_version_id
from app.fetch.guard import validate_url
from app.store.models import DocumentVersion, Source
from tests.integration.conftest import (
    FIXTURE_HOST,
    Harness,
    html_response,
    read_html_fixture,
)

pytestmark = pytest.mark.integration

_PATH = "/guide"
_HTML_A = read_html_fixture("python_tutorial_classes.html")
_HTML_B = read_html_fixture("pydantic_models.html")


def _resolve(harness: Harness, *, force_refresh: bool = False) -> dict:
    response = harness.client.post(
        "/v1/documents/resolve",
        json={"url": harness.url(_PATH), "force_refresh": force_refresh},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _version_count(harness: Harness, source_id: str) -> int:
    with harness.session_factory() as session:
        stmt = (
            select(func.count())
            .select_from(DocumentVersion)
            .where(DocumentVersion.source_id == source_id)
        )
        return session.scalar(stmt) or 0


def _normalized_url(harness: Harness) -> str:
    return validate_url(
        harness.url(_PATH), resolver=harness.resolver, settings=harness.settings
    ).normalized_url


def test_resolve_first_fetch_miss(harness: Harness, blob_store) -> None:
    """First resolve: 200, cache_hit False, source+version rows + S3 blobs."""
    harness.transport.set(_PATH, lambda: html_response(_HTML_A))

    body = _resolve(harness)

    assert body["stats"]["cache_hit"] is False
    assert body["document_id"].startswith("doc_")
    assert body["version_id"].startswith("ver_")
    assert body["trust"] == "untrusted_source"
    assert body["markdown"]  # markdown requested by default
    assert body["blocks"] is not None

    normalized = _normalized_url(harness)
    document_id = make_document_id(normalized)
    assert body["document_id"] == document_id

    # Rows persisted.
    with harness.session_factory() as session:
        source = session.get(Source, document_id)
        assert source is not None
        assert source.domain == FIXTURE_HOST
        assert source.current_version_id == body["version_id"]
    assert _version_count(harness, document_id) == 1

    # Blobs present in S3 (round-trip through the real store).
    version_id = body["version_id"]
    assert blob_store.get_raw(version_id) == _HTML_A
    doc = blob_store.get_ir(version_id)
    assert doc.document_id == document_id


def test_resolve_cache_hit_no_network(harness: Harness) -> None:
    """Second resolve inside the TTL -> cache_hit True AND zero network calls."""
    harness.transport.set(_PATH, lambda: html_response(_HTML_A))

    first = _resolve(harness)
    assert first["stats"]["cache_hit"] is False
    calls_after_first = harness.transport.calls
    assert calls_after_first > 0  # the miss hit the network (robots + body)

    second = _resolve(harness)
    assert second["stats"]["cache_hit"] is True
    # The whole point: a cache hit performs NO network I/O.
    assert harness.transport.calls == calls_after_first
    # Same document/version served from storage.
    assert second["document_id"] == first["document_id"]
    assert second["version_id"] == first["version_id"]


def test_resolve_304_not_modified(harness: Harness) -> None:
    """A 304 only refreshes fetched_at: no new version, cache_hit False."""
    harness.transport.set(_PATH, lambda: html_response(_HTML_A))
    first = _resolve(harness)
    document_id = first["document_id"]
    assert _version_count(harness, document_id) == 1

    # Expire the cache so the second call fetches, and script a 304.
    harness.clock.advance(harness.settings.resolve_cache_ttl_seconds + 1)
    harness.transport.set(_PATH, lambda: httpx.Response(304))

    second = _resolve(harness)
    assert second["stats"]["cache_hit"] is False
    # No new version created.
    assert _version_count(harness, document_id) == 1
    assert second["version_id"] == first["version_id"]


def test_resolve_changed_content_new_version(harness: Harness) -> None:
    """A different body after TTL -> new content_hash -> a second version."""
    harness.transport.set(_PATH, lambda: html_response(_HTML_A))
    first = _resolve(harness)
    document_id = first["document_id"]

    harness.clock.advance(harness.settings.resolve_cache_ttl_seconds + 1)
    harness.transport.set(_PATH, lambda: html_response(_HTML_B))
    second = _resolve(harness)

    assert second["stats"]["cache_hit"] is False
    assert second["version_id"] != first["version_id"]
    assert _version_count(harness, document_id) == 2
    with harness.session_factory() as session:
        source = session.get(Source, document_id)
        assert source is not None
        assert source.current_version_id == second["version_id"]


def test_resolve_same_hash_no_new_version(harness: Harness) -> None:
    """An identical body after TTL -> same content_hash -> no new version."""
    harness.transport.set(_PATH, lambda: html_response(_HTML_A))
    first = _resolve(harness)
    document_id = first["document_id"]

    harness.clock.advance(harness.settings.resolve_cache_ttl_seconds + 1)
    # Same bytes, so content_hash is unchanged -> dedup.
    second = _resolve(harness)

    assert second["stats"]["cache_hit"] is False
    assert second["version_id"] == first["version_id"]
    assert _version_count(harness, document_id) == 1


def test_resolve_stats_populated(harness: Harness) -> None:
    """A real HTML fixture populates both token counters and the block count."""
    harness.transport.set(_PATH, lambda: html_response(_HTML_A))
    body = _resolve(harness)

    stats = body["stats"]
    assert stats["tokens_raw_html"] > 0
    assert stats["tokens_markdown"] > 0
    assert stats["blocks"] == len(body["blocks"])
    assert stats["blocks"] > 0


def test_resolve_version_id_matches_content_hash(harness: Harness) -> None:
    """version_id is derived from the sha256 of the raw body (determinism)."""
    harness.transport.set(_PATH, lambda: html_response(_HTML_A))
    body = _resolve(harness)

    expected = make_version_id(body["document_id"], hashlib.sha256(_HTML_A).hexdigest())
    assert body["version_id"] == expected
