"""End-to-end test: the NINA SDK against the local API — M9 FEAT-004 (SPEC §8).

Proves the e2e acceptance criterion: :class:`NinaClient` (the installable
``nina-client`` SDK) pointed at the LOCAL API — auth ENFORCED (``env=production``
via the auth settings seam), providers FAKE, a key minted through
:class:`ApiKeyRepo` — receives fragments with full provenance. The SDK's own
``httpx`` client is driven by an :class:`httpx.ASGITransport` wrapping the test
app, so there is ZERO external network and ZERO real keys. HTTP to the source
pages is itself mocked by the instrumented ``ScriptedTransport`` and embeddings/
reranking use the deterministic fakes (CLAUDE.md). Postgres + SeaweedFS are real.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.api import assemble, auth, documents, usage
from app.config import Settings
from app.main import create_app
from app.providers.embeddings.fake import FakeEmbeddingProvider
from app.providers.rerank.fake import FakeRerankProvider
from app.store.blobs import BlobStore
from app.usage.keys import ApiKeyRepo
from app.usage.limits import TokenBucketLimiter
from tests.integration.conftest import (
    FIXTURE_HOST,
    FakeClock,
    ScriptedTransport,
    _make_robots_checker,
    _truncate,
    html_response,
    read_html_fixture,
)
from tests.unit.fetch._resolvers import FakeResolver

# The SDK is a SEPARATE installable distribution (sdk/python/nina_client), not on
# sys.path by default; add it the same way tests/unit/sdk/conftest.py does so the
# e2e exercises the real published client (no install, no main-build coupling).
_SDK_SRC = Path(__file__).resolve().parents[2] / "sdk" / "python"
if str(_SDK_SRC) not in sys.path:
    sys.path.insert(0, str(_SDK_SRC))

from nina_client import NinaClient  # noqa: E402 — path set just above

pytestmark = pytest.mark.integration

_AUTH_PATH = "/auth"
_ERRORS_PATH = "/errors"
_HTML_A = read_html_fixture("python_tutorial_classes.html")
_HTML_B = read_html_fixture("pydantic_models.html")

#: Base URL the ASGI transport serves the app under (never hits the network).
_SDK_BASE_URL = "http://nina.local"


class _FrozenMonotonic:
    """A frozen monotonic clock so the token bucket never refills mid-test."""

    def __call__(self) -> float:
        return 0.0


class _TestClientTransport(httpx.BaseTransport):
    """A sync httpx transport that drives the app through a Starlette TestClient.

    The SDK uses a plain synchronous ``httpx.Client``, so ``httpx.ASGITransport``
    (async-only) cannot be used. This adapter replays each request through the
    in-process :class:`TestClient` and returns a clean byte-buffered
    :class:`httpx.Response`, so no external network is touched.
    """

    def __init__(self, test_client: TestClient) -> None:
        self._test_client = test_client

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        response = self._test_client.request(
            request.method,
            str(request.url),
            headers=request.headers,
            content=request.read(),
        )
        return httpx.Response(
            status_code=response.status_code,
            headers=response.headers,
            content=response.content,
            request=request,
        )


@dataclass
class SdkHarness:
    """The SDK client wired to the local app + the source-page transport."""

    client: NinaClient
    transport: ScriptedTransport
    base_url: str = f"http://{FIXTURE_HOST}"

    def url(self, path: str) -> str:
        return f"{self.base_url}{path}"


def _mint_key(session_factory: sessionmaker[Session], name: str) -> str:
    session = session_factory()
    try:
        _api_key, plaintext = ApiKeyRepo(session).create(name)
        session.commit()
        return plaintext
    finally:
        session.close()


def _truncate_keys(engine: Engine) -> None:
    from sqlalchemy import text

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM usage_events"))
        conn.execute(text("DELETE FROM api_keys"))


@pytest.fixture
def sdk_harness(
    engine: Engine,
    session_factory: sessionmaker[Session],
    blob_store: BlobStore,
    resolver: FakeResolver,
    clock: FakeClock,
    settings: Settings,
) -> Iterator[SdkHarness]:
    _truncate(engine)
    _truncate_keys(engine)
    transport = ScriptedTransport()
    transport.set(_AUTH_PATH, lambda: html_response(_HTML_A))
    transport.set(_ERRORS_PATH, lambda: html_response(_HTML_B))

    app = create_app()
    prod_settings = settings.model_copy(update={"env": "production"})
    limiter = TokenBucketLimiter(
        prod_settings.model_copy(update={"rate_limit_capacity": 100}),
        time_fn=_FrozenMonotonic(),
    )

    # Resolve/assemble seams: HTTP mocked, DB/S3 real, providers fake. The
    # documents/assemble handlers stay in dev so the ``selection=truncate`` flag
    # is accepted (auth is enforced independently via its own seam below).
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
    app.dependency_overrides[assemble.provide_embeddings] = FakeEmbeddingProvider
    app.dependency_overrides[assemble.provide_reranker] = FakeRerankProvider
    # Auth seams: enforce production against the real test DB (so a real Bearer
    # key is required — exactly what the SDK sends).
    app.dependency_overrides[auth.provide_settings] = lambda: prod_settings
    app.dependency_overrides[auth.provide_auth_session_factory] = lambda: (
        session_factory
    )
    # Usage seams: fresh limiter + the test session factory.
    app.dependency_overrides[usage.provide_limiter] = lambda: limiter
    app.dependency_overrides[usage.provide_usage_settings] = lambda: prod_settings
    app.dependency_overrides[usage.provide_usage_session_factory] = lambda: (
        session_factory
    )

    key = _mint_key(session_factory, "sdk-pilot")
    # The SDK talks to the app over its OWN httpx client. We point that client at
    # a SYNChronous in-process transport by reusing Starlette's TestClient
    # transport (the SDK uses a sync httpx.Client, so httpx.ASGITransport — which
    # is async-only — cannot be used here). Zero network, a real minted key.
    test_client = TestClient(app)
    sdk_client = NinaClient(
        _SDK_BASE_URL,
        key,
        transport=_TestClientTransport(test_client),
        sleep=lambda _seconds: None,
    )
    try:
        with test_client:
            yield SdkHarness(client=sdk_client, transport=transport)
    finally:
        sdk_client.close()
        app.dependency_overrides.clear()
        _truncate_keys(engine)
        _truncate(engine)


def test_sdk_assemble_receives_fragments_with_provenance(
    sdk_harness: SdkHarness,
) -> None:
    harness = sdk_harness

    result = harness.client.assemble(
        "How are classes defined and used?",
        [harness.url(_AUTH_PATH), harness.url(_ERRORS_PATH)],
        max_tokens=4000,
    )

    assert result.trust == "untrusted_source"
    assert result.fragments, "expected at least one fragment"
    for fragment in result.fragments:
        assert fragment.document_id.startswith("doc_")
        assert fragment.version_id.startswith("ver_")
        assert fragment.source_url in {
            harness.url(_AUTH_PATH),
            harness.url(_ERRORS_PATH),
        }
        # Provenance includes literal evidence quotes (<=300 chars each).
        for ev in fragment.evidence:
            assert ev.quote
            assert len(ev.quote) <= 300

    # Per-URL provenance is surfaced and both sources resolved OK.
    statuses = {s.url: s.status for s in result.sources}
    assert statuses[harness.url(_AUTH_PATH)] == "ok"
    assert statuses[harness.url(_ERRORS_PATH)] == "ok"
    assert result.token_usage.output_tokens <= 4000


def test_sdk_resolve_receives_document_with_provenance(
    sdk_harness: SdkHarness,
) -> None:
    harness = sdk_harness

    result = harness.client.resolve(harness.url(_AUTH_PATH), formats=["markdown"])

    assert result.document_id.startswith("doc_")
    assert result.version_id.startswith("ver_")
    assert result.source.url == harness.url(_AUTH_PATH)
    assert result.markdown
    assert result.trust == "untrusted_source"
