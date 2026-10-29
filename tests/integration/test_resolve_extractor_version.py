"""Extractor upgrades re-extract unchanged bytes (SPEC §4/§5, DECISIONS 2026-10-29).

Before migration 0006 the version id was ``ver_ + sha256(raw)[:16]`` and the
cache-hit, 304 and same-hash paths served the stored IR, so a page whose bytes
did not change kept its OLD extraction (and chunks) after an extractor upgrade.
A version is now one (document, raw bytes, ``EXTRACTOR_VERSION``): these tests
simulate a bump by patching ``app.extract.ir.EXTRACTOR_VERSION`` and wrapping
the HTML extractor so the "new extractor" leaves a visible mark in the IR.

Also covered: two URLs serving identical bytes, and a page that goes back to
bytes it served before (A -> B -> A), which used to collide on the version
primary key.
"""

from __future__ import annotations

import hashlib
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select, text

from app.api import documents
from app.extract import ir
from app.extract.ir import Document, make_document_id, make_version_id
from app.fetch.render import RenderResult
from app.store.models import DocumentVersion, Source
from tests.integration.conftest import Harness, html_response, read_html_fixture

pytestmark = pytest.mark.integration

_PATH = "/guide"
_HTML_A = read_html_fixture("python_tutorial_classes.html")
_HTML_B = read_html_fixture("pydantic_models.html")
_NEW_VERSION = "999-test"
_NEW_TITLE = "extracted by the new extractor"


def _resolve(harness: Harness, path: str = _PATH, **extra: Any) -> dict[str, Any]:
    response = harness.client.post(
        "/v1/documents/resolve", json={"url": harness.url(path), **extra}
    )
    assert response.status_code == 200, response.text
    return response.json()


def _versions(harness: Harness, document_id: str) -> dict[str, DocumentVersion]:
    with harness.session_factory() as session:
        rows = session.scalars(
            select(DocumentVersion).where(DocumentVersion.source_id == document_id)
        ).all()
        session.expunge_all()
        return {row.id: row for row in rows}


def _current(harness: Harness, document_id: str) -> str | None:
    with harness.session_factory() as session:
        source = session.get(Source, document_id)
        assert source is not None
        return source.current_version_id


def _bump_extractor(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulate a release with a new extractor: new version + visible output."""
    monkeypatch.setattr(ir, "EXTRACTOR_VERSION", _NEW_VERSION)
    original = documents.extract_html

    def new_extract_html(html: str, source: ir.SourceInfo) -> Document:
        doc = original(html, source)
        doc.metadata.title = _NEW_TITLE
        return doc

    monkeypatch.setattr(documents, "extract_html", new_extract_html)


def test_bump_reextracts_stored_bytes_on_cache_hit(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fresh cache + new extractor: new version from the STORED raw bytes, no
    network, old version (row, IR blob) untouched."""
    harness.transport.set(_PATH, lambda: html_response(_HTML_A))
    first = _resolve(harness)
    document_id = first["document_id"]
    content_hash = hashlib.sha256(_HTML_A).hexdigest()
    assert first["version_id"] == make_version_id(document_id, content_hash)
    old_title = first["metadata"]["title"]
    assert old_title != _NEW_TITLE
    calls = harness.transport.calls
    original_version = ir.EXTRACTOR_VERSION

    _bump_extractor(monkeypatch)
    second = _resolve(harness)

    assert harness.transport.calls == calls  # re-extracted without any fetch
    assert second["stats"]["cache_hit"] is True
    assert second["version_id"] == make_version_id(
        document_id, content_hash, _NEW_VERSION
    )
    assert second["version_id"] != first["version_id"]
    assert second["metadata"]["title"] == _NEW_TITLE
    assert second["source"]["content_hash"] == content_hash
    assert second["source"]["fetched_at"] == first["source"]["fetched_at"]
    assert all(b["block_id"].startswith("b_") for b in second["blocks"])

    rows = _versions(harness, document_id)
    assert set(rows) == {first["version_id"], second["version_id"]}
    assert rows[first["version_id"]].extractor_version == original_version
    assert rows[second["version_id"]].extractor_version == _NEW_VERSION
    assert rows[second["version_id"]].content_type_header == (
        "text/html; charset=utf-8"
    )
    assert _current(harness, document_id) == second["version_id"]

    # The old version is untouched: same IR blob, still served by id.
    old_doc = harness.blob_store.get_ir(first["version_id"])
    assert old_doc.metadata.title == old_title
    assert old_doc.version_id == first["version_id"]
    new_doc = harness.blob_store.get_ir(second["version_id"])
    assert new_doc.version_id == second["version_id"]
    assert harness.blob_store.get_raw(second["version_id"]) == _HTML_A
    got = harness.client.get(
        f"/v1/documents/{document_id}", params={"version_id": first["version_id"]}
    )
    assert got.status_code == 200
    assert got.json()["metadata"]["title"] == old_title

    # And the new version is now a plain cache hit (no third version).
    third = _resolve(harness)
    assert third["version_id"] == second["version_id"]
    assert len(_versions(harness, document_id)) == 2


def test_legacy_row_without_extractor_version_is_reextracted(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rows from before migration 0006 (NULL) count as outdated."""
    harness.transport.set(_PATH, lambda: html_response(_HTML_A))
    # Legacy rows carry ids of the old derivation: store one under another id.
    current_version = ir.EXTRACTOR_VERSION
    monkeypatch.setattr(ir, "EXTRACTOR_VERSION", "legacy-simulation")
    first = _resolve(harness)
    monkeypatch.setattr(ir, "EXTRACTOR_VERSION", current_version)
    with harness.session_factory() as session, session.begin():
        session.execute(
            text(
                "UPDATE document_versions SET extractor_version = NULL, "
                "content_type_header = NULL WHERE id = :id"
            ),
            {"id": first["version_id"]},
        )
    calls = harness.transport.calls

    second = _resolve(harness)

    assert harness.transport.calls == calls
    rows = _versions(harness, first["document_id"])
    assert len(rows) == 2
    assert rows[first["version_id"]].extractor_version is None
    new_id = second["version_id"]
    assert new_id != first["version_id"]
    assert rows[new_id].extractor_version == ir.EXTRACTOR_VERSION
    # Charset sniffed from the body when the header was never stored.
    assert second["markdown"] == first["markdown"]


def test_bump_with_unchanged_bytes_after_ttl_creates_new_version(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stale cache, same bytes, new extractor: same-hash dedup must not apply."""
    harness.transport.set(_PATH, lambda: html_response(_HTML_A))
    first = _resolve(harness)
    harness.clock.advance(harness.settings.resolve_cache_ttl_seconds + 1)
    _bump_extractor(monkeypatch)

    second = _resolve(harness)

    assert second["stats"]["cache_hit"] is False
    assert second["metadata"]["title"] == _NEW_TITLE
    assert second["version_id"] != first["version_id"]
    assert second["source"]["fetched_at"] != first["source"]["fetched_at"]
    rows = _versions(harness, first["document_id"])
    assert len(rows) == 2
    assert _current(harness, first["document_id"]) == second["version_id"]


def test_bump_with_304_reextracts_stored_bytes(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 304 on an outdated extraction re-extracts the stored body."""
    harness.transport.set(_PATH, lambda: html_response(_HTML_A))
    first = _resolve(harness)
    harness.clock.advance(harness.settings.resolve_cache_ttl_seconds + 1)
    harness.transport.set(_PATH, lambda: httpx.Response(304))
    _bump_extractor(monkeypatch)

    second = _resolve(harness)

    assert second["metadata"]["title"] == _NEW_TITLE
    assert second["version_id"] == make_version_id(
        first["document_id"], hashlib.sha256(_HTML_A).hexdigest(), _NEW_VERSION
    )
    assert len(_versions(harness, first["document_id"])) == 2


def test_same_extractor_still_dedups(harness: Harness) -> None:
    """No bump: unchanged bytes after the TTL keep the one version."""
    harness.transport.set(_PATH, lambda: html_response(_HTML_A))
    first = _resolve(harness)
    harness.clock.advance(harness.settings.resolve_cache_ttl_seconds + 1)
    second = _resolve(harness)
    assert second["version_id"] == first["version_id"]
    assert len(_versions(harness, first["document_id"])) == 1


def test_bump_rerenders_a_browser_rendered_version(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The rendered DOM is never stored: re-extraction re-runs the pipeline on
    the stored HTTP body, which renders again (robots checked first)."""
    renders: list[str] = []

    def fake_render(url: str, **_kwargs: object) -> RenderResult:
        renders.append(url)
        return RenderResult(
            final_url=url,
            html="<html><body><main><h1>Rendered</h1>"
            "<p>Rendered body text from the browser.</p></main></body></html>",
            status=200,
        )

    monkeypatch.setattr(documents, "render_url", fake_render)
    harness.transport.set(_PATH, lambda: html_response(_HTML_A))
    first = _resolve(harness, render="always")
    assert first["source"]["render_mode"] == "browser"
    assert len(renders) == 1
    calls = harness.transport.calls

    _bump_extractor(monkeypatch)
    second = _resolve(harness, render="always")

    assert len(renders) == 2  # rendered again
    assert second["source"]["render_mode"] == "browser"
    assert second["metadata"]["title"] == _NEW_TITLE
    assert second["version_id"] != first["version_id"]
    # Only robots.txt could be consulted (cached here); the page was not refetched.
    assert _PATH not in harness.transport.paths[calls:]


def test_two_urls_with_identical_bytes_get_their_own_versions(
    harness: Harness,
) -> None:
    """The same bytes at two URLs used to share one version id (primary key
    collision, and the second IR blob overwrote the first)."""
    harness.transport.set("/a", lambda: html_response(_HTML_A))
    harness.transport.set("/b", lambda: html_response(_HTML_A))

    a = _resolve(harness, "/a")
    b = _resolve(harness, "/b")

    assert a["version_id"] != b["version_id"]
    assert harness.blob_store.get_ir(a["version_id"]).document_id == a["document_id"]
    assert harness.blob_store.get_ir(b["version_id"]).document_id == b["document_id"]
    assert a["document_id"] == make_document_id(harness.url("/a"))


def test_page_returning_to_earlier_bytes_reuses_that_version(
    harness: Harness,
) -> None:
    """A -> B -> A: the third resolve makes the first version current again
    instead of inserting a duplicate id."""
    harness.transport.set(_PATH, lambda: html_response(_HTML_A))
    first = _resolve(harness)
    harness.clock.advance(harness.settings.resolve_cache_ttl_seconds + 1)
    harness.transport.set(_PATH, lambda: html_response(_HTML_B))
    second = _resolve(harness)
    harness.clock.advance(harness.settings.resolve_cache_ttl_seconds + 1)
    harness.transport.set(_PATH, lambda: html_response(_HTML_A))

    third = _resolve(harness)

    assert second["version_id"] != first["version_id"]
    assert third["version_id"] == first["version_id"]
    assert _current(harness, first["document_id"]) == first["version_id"]
    with harness.session_factory() as session:
        count = session.scalar(
            select(func.count())
            .select_from(DocumentVersion)
            .where(DocumentVersion.source_id == first["document_id"])
        )
    assert count == 2
    # fetched_at moved forward, so the reused version is fresh again.
    fourth = _resolve(harness)
    assert fourth["stats"]["cache_hit"] is True
