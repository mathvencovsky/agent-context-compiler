"""Integration tests for POST /v1/context/assemble — M7 (SPEC §6/§8).

HTTP is ALWAYS mocked through the instrumented ``ScriptedTransport`` (see
``conftest.py``); no test touches the internet. Postgres + pgvector and
S3/SeaweedFS are real (compose). Embeddings use the deterministic
:class:`FakeEmbeddingProvider` and reranking uses :class:`FakeRerankProvider`,
so no provider ever reaches the network (CLAUDE.md).

The cases cover the SPEC §8 contract: a happy-path 200 whose fragments carry
full provenance + scores, a partial failure that stays 200 with the dead URL in
``sources[]`` + ``warnings``, an all-fail 422 envelope, and ablation flags
rejected when ``ENV == "production"``.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.api import assemble, documents
from app.config import Settings
from app.main import create_app
from app.providers.embeddings.fake import FakeEmbeddingProvider
from app.providers.rerank.fake import FakeRerankProvider
from app.store.blobs import BlobStore
from tests.integration.conftest import (
    FIXTURE_HOST,
    FakeClock,
    ScriptedTransport,
    html_response,
    read_html_fixture,
)
from tests.unit.fetch._resolvers import FakeResolver

pytestmark = pytest.mark.integration

_AUTH_PATH = "/auth"
_ERRORS_PATH = "/errors"
_DEAD_PATH = "/dead"
_HTML_A = read_html_fixture("python_tutorial_classes.html")
_HTML_B = read_html_fixture("pydantic_models.html")


@dataclass
class AssembleHarness:
    """TestClient + transport for the assemble endpoint, all seams overridden."""

    client: TestClient
    transport: ScriptedTransport
    clock: FakeClock
    base_url: str = f"http://{FIXTURE_HOST}"

    def url(self, path: str) -> str:
        return f"{self.base_url}{path}"


@pytest.fixture
def assemble_harness(
    engine: Engine,
    session_factory: sessionmaker[Session],
    blob_store: BlobStore,
    resolver: FakeResolver,
    clock: FakeClock,
    settings: Settings,
) -> Iterator[AssembleHarness]:
    from tests.integration.conftest import _make_robots_checker, _truncate

    _truncate(engine)
    transport = ScriptedTransport()
    app = create_app()

    # Resolve-path seams (shared with the documents router).
    app.dependency_overrides[documents.provide_session_factory] = lambda: (
        session_factory
    )
    app.dependency_overrides[documents.provide_blob_store] = lambda: blob_store
    app.dependency_overrides[documents.provide_resolver] = lambda: resolver
    app.dependency_overrides[documents.provide_transport] = lambda: transport.transport
    app.dependency_overrides[documents.provide_now] = lambda: clock.now
    app.dependency_overrides[documents.provide_settings] = lambda: settings
    app.dependency_overrides[documents.provide_robots_checker] = lambda: (
        _make_robots_checker(transport, resolver, settings)
    )
    # Provider seams specific to assemble (deterministic fakes, no network).
    app.dependency_overrides[assemble.provide_embeddings] = FakeEmbeddingProvider
    app.dependency_overrides[assemble.provide_reranker] = FakeRerankProvider

    with TestClient(app) as client:
        yield AssembleHarness(client=client, transport=transport, clock=clock)
    app.dependency_overrides.clear()
    _truncate(engine)


def _post(harness: AssembleHarness, body: dict) -> object:
    return harness.client.post("/v1/context/assemble", json=body)


def test_assemble_happy_path(assemble_harness: AssembleHarness) -> None:
    """Two good URLs -> 200 with fragments carrying provenance + scores."""
    harness = assemble_harness
    harness.transport.set(_AUTH_PATH, lambda: html_response(_HTML_A))
    harness.transport.set(_ERRORS_PATH, lambda: html_response(_HTML_B))

    response = _post(
        harness,
        {
            "query": "How are classes defined and used?",
            "urls": [harness.url(_AUTH_PATH), harness.url(_ERRORS_PATH)],
            "max_tokens": 4000,
            # Force the no-min_value path so a context is produced with the fake
            # reranker's low overlap scores (still the full retrieval pipeline).
            "selection": "truncate",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["trust"] == "untrusted_source"
    assert body["query"] == "How are classes defined and used?"
    assert body["token_usage"]["budget_tokens"] == 4000
    assert body["token_usage"]["tokenizer"] == "cl100k_base (approximate)"
    assert body["coverage"] in {"insufficient", "partial", "sufficient"}

    # Both sources resolved OK.
    statuses = {s["url"]: s["status"] for s in body["sources"]}
    assert statuses[harness.url(_AUTH_PATH)] == "ok"
    assert statuses[harness.url(_ERRORS_PATH)] == "ok"

    # Fragments carry full provenance + scores + evidence.
    assert body["fragments"], "expected at least one fragment"
    assert body["token_usage"]["output_tokens"] <= 4000
    for frag in body["fragments"]:
        assert frag["chunk_id"]
        assert frag["document_id"].startswith("doc_")
        assert frag["version_id"].startswith("ver_")
        assert frag["source_url"] in {
            harness.url(_AUTH_PATH),
            harness.url(_ERRORS_PATH),
        }
        assert "rrf" in frag["scores"]
        for ev in frag["evidence"]:
            assert len(ev["quote"]) <= 300


def test_assemble_partial_failure_stays_200(
    assemble_harness: AssembleHarness,
) -> None:
    """One good + one dead URL -> 200 with the failure in sources + warnings."""
    harness = assemble_harness
    harness.transport.set(_AUTH_PATH, lambda: html_response(_HTML_A))
    # _DEAD_PATH is not scripted -> the transport returns 404 -> fetch fails.

    response = _post(
        harness,
        {
            "query": "How are classes defined and used?",
            "urls": [harness.url(_AUTH_PATH), harness.url(_DEAD_PATH)],
            "selection": "truncate",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()

    statuses = {s["url"]: s["status"] for s in body["sources"]}
    assert statuses[harness.url(_AUTH_PATH)] == "ok"
    assert statuses[harness.url(_DEAD_PATH)] != "ok"

    # The failure is surfaced explicitly (never a silent empty result).
    warned_urls = {w.get("url") for w in body["warnings"]}
    assert harness.url(_DEAD_PATH) in warned_urls


def test_assemble_all_fail_422(assemble_harness: AssembleHarness) -> None:
    """Every URL dead -> 422 with the SPEC §8 error envelope."""
    harness = assemble_harness
    # Neither path is scripted -> both fetches fail.
    response = _post(
        harness,
        {
            "query": "anything",
            "urls": [harness.url(_DEAD_PATH), harness.url("/also-dead")],
        },
    )
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["error"]["code"] == "all_sources_failed"
    assert "message" in body["error"]


def test_assemble_ablation_rejected_in_production(
    assemble_harness: AssembleHarness, settings: Settings
) -> None:
    """A non-default ablation flag in production -> 400 ablation_not_allowed."""
    harness = assemble_harness
    harness.transport.set(_AUTH_PATH, lambda: html_response(_HTML_A))

    # Flip env to production for this request only via a settings override.
    prod_settings = settings.model_copy(update={"env": "production"})
    harness.client.app.dependency_overrides[documents.provide_settings] = lambda: (
        prod_settings
    )

    response = _post(
        harness,
        {
            "query": "How are classes defined?",
            "urls": [harness.url(_AUTH_PATH)],
            "use_rerank": False,
        },
    )
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "ablation_not_allowed"
