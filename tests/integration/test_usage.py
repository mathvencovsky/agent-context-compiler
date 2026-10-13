"""Integration tests for usage accounting + rate limit on /v1 — M9 (SPEC §8).

Auth is ENFORCED (``env=production`` via the auth settings seam); a valid key is
minted through :class:`ApiKeyRepo`. HTTP is mocked through the instrumented
``ScriptedTransport`` (no internet), embeddings/reranking use the deterministic
fakes (no provider network — CLAUDE.md). Postgres + SeaweedFS are real.

Covered (SPEC §8): every /v1 request writes exactly one ``usage_events`` row;
``GET /v1/usage`` aggregates the current key's events by day and by endpoint with
p50/p95 and delivered tokens; exceeding the per-key token bucket returns 429 with
a ``Retry-After`` header. The limiter is injected fresh per test (controllable
capacity) so bucket state never leaks between cases.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
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

pytestmark = pytest.mark.integration

_AUTH_PATH = "/auth"
_ERRORS_PATH = "/errors"
_HTML_A = read_html_fixture("python_tutorial_classes.html")
_HTML_B = read_html_fixture("pydantic_models.html")


class FakeMonotonic:
    """A frozen monotonic clock so the token bucket never refills mid-test."""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


@dataclass
class UsageHarness:
    """TestClient + transport + session factory + the injected limiter."""

    client: TestClient
    transport: ScriptedTransport
    session_factory: sessionmaker[Session]
    limiter: TokenBucketLimiter
    key: str
    base_url: str = f"http://{FIXTURE_HOST}"

    def url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def auth_header(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.key}"}


def _truncate_keys(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM usage_events"))
        conn.execute(text("DELETE FROM api_keys"))


def _mint_key(
    session_factory: sessionmaker[Session],
    name: str,
    limits: dict[str, object] | None = None,
) -> str:
    session = session_factory()
    try:
        _api_key, plaintext = ApiKeyRepo(session).create(name, limits)
        session.commit()
        return plaintext
    finally:
        session.close()


def _make_harness(
    engine: Engine,
    session_factory: sessionmaker[Session],
    blob_store: BlobStore,
    resolver: FakeResolver,
    clock: FakeClock,
    settings: Settings,
    *,
    capacity: int,
    limits: dict[str, object] | None = None,
) -> UsageHarness:
    _truncate(engine)
    _truncate_keys(engine)
    transport = ScriptedTransport()
    transport.set(_AUTH_PATH, lambda: html_response(_HTML_A))
    transport.set(_ERRORS_PATH, lambda: html_response(_HTML_B))

    app = create_app()
    prod_settings = settings.model_copy(update={"env": "production"})
    limiter = TokenBucketLimiter(
        prod_settings.model_copy(update={"rate_limit_capacity": capacity}),
        time_fn=FakeMonotonic(),
    )

    # Resolve-path seams.
    app.dependency_overrides[documents.provide_session_factory] = lambda: (
        session_factory
    )
    app.dependency_overrides[documents.provide_blob_store] = lambda: blob_store
    app.dependency_overrides[documents.provide_resolver] = lambda: resolver
    app.dependency_overrides[documents.provide_transport] = lambda: transport.transport
    app.dependency_overrides[documents.provide_now] = lambda: clock.now
    # The documents/assemble handlers run in dev so the ``selection=truncate``
    # ablation flag is accepted and a context is produced; auth is enforced
    # independently via its own settings seam below (production).
    app.dependency_overrides[documents.provide_settings] = lambda: settings
    app.dependency_overrides[documents.provide_robots_checker] = lambda: (
        _make_robots_checker(transport, resolver, settings)
    )
    # Provider seams (deterministic fakes).
    app.dependency_overrides[assemble.provide_embeddings] = FakeEmbeddingProvider
    app.dependency_overrides[assemble.provide_reranker] = FakeRerankProvider
    # Auth seams: enforce production against the real test DB.
    app.dependency_overrides[auth.provide_settings] = lambda: prod_settings
    app.dependency_overrides[auth.provide_auth_session_factory] = lambda: (
        session_factory
    )
    # Usage seams: fresh limiter + settings + the test session factory.
    app.dependency_overrides[usage.provide_limiter] = lambda: limiter
    app.dependency_overrides[usage.provide_usage_settings] = lambda: prod_settings
    app.dependency_overrides[usage.provide_usage_session_factory] = lambda: (
        session_factory
    )

    key = _mint_key(session_factory, "usage-pilot", limits)
    client = TestClient(app)
    return UsageHarness(
        client=client,
        transport=transport,
        session_factory=session_factory,
        limiter=limiter,
        key=key,
    )


@pytest.fixture
def usage_harness(
    engine: Engine,
    session_factory: sessionmaker[Session],
    blob_store: BlobStore,
    resolver: FakeResolver,
    clock: FakeClock,
    settings: Settings,
) -> Iterator[UsageHarness]:
    harness = _make_harness(
        engine,
        session_factory,
        blob_store,
        resolver,
        clock,
        settings,
        capacity=100,
    )
    with harness.client:
        yield harness
    harness.client.app.dependency_overrides.clear()
    _truncate_keys(engine)
    _truncate(engine)


@pytest.fixture
def tight_harness(
    engine: Engine,
    session_factory: sessionmaker[Session],
    blob_store: BlobStore,
    resolver: FakeResolver,
    clock: FakeClock,
    settings: Settings,
) -> Iterator[UsageHarness]:
    # Capacity 2: the third request in a burst trips the limiter.
    harness = _make_harness(
        engine,
        session_factory,
        blob_store,
        resolver,
        clock,
        settings,
        capacity=2,
    )
    with harness.client:
        yield harness
    harness.client.app.dependency_overrides.clear()
    _truncate_keys(engine)
    _truncate(engine)


def _resolve_body(path: str) -> dict[str, object]:
    return {"url": f"http://{FIXTURE_HOST}{path}", "formats": ["markdown"]}


def _assemble_body(harness: UsageHarness) -> dict[str, object]:
    return {
        "query": "How are classes defined and used?",
        "urls": [harness.url(_AUTH_PATH), harness.url(_ERRORS_PATH)],
        "max_tokens": 4000,
        "selection": "truncate",
    }


def _count_events(session_factory: sessionmaker[Session], endpoints: list[str]) -> int:
    session = session_factory()
    try:
        rows = session.execute(
            text("SELECT count(*) FROM usage_events WHERE endpoint = ANY(:eps)"),
            {"eps": endpoints},
        ).scalar_one()
        return int(rows)
    finally:
        session.close()


def test_requests_write_one_usage_event_each_and_usage_aggregates(
    usage_harness: UsageHarness,
) -> None:
    harness = usage_harness

    r1 = harness.client.post(
        "/v1/documents/resolve",
        json=_resolve_body(_AUTH_PATH),
        headers=harness.auth_header(),
    )
    assert r1.status_code == 200, r1.text
    r2 = harness.client.post(
        "/v1/documents/resolve",
        json=_resolve_body(_ERRORS_PATH),
        headers=harness.auth_header(),
    )
    assert r2.status_code == 200, r2.text
    r3 = harness.client.post(
        "/v1/context/assemble",
        json=_assemble_body(harness),
        headers=harness.auth_header(),
    )
    assert r3.status_code == 200, r3.text

    # Exactly three resolve/assemble usage events were written.
    assert (
        _count_events(
            harness.session_factory,
            ["/v1/documents/resolve", "/v1/context/assemble"],
        )
        == 3
    )

    # GET /v1/usage aggregates by day and by endpoint.
    usage_resp = harness.client.get("/v1/usage", headers=harness.auth_header())
    assert usage_resp.status_code == 200, usage_resp.text
    body = usage_resp.json()

    assert body["requests"] >= 3
    assert len(body["by_day"]) == 1
    day = body["by_day"][0]
    endpoints = {e["endpoint"]: e for e in day["endpoints"]}
    assert "/v1/documents/resolve" in endpoints
    assert "/v1/context/assemble" in endpoints
    assert endpoints["/v1/documents/resolve"]["requests"] == 2
    assert endpoints["/v1/context/assemble"]["requests"] == 1
    # The assemble endpoint delivered some tokens; p50/p95 are present ints.
    assert endpoints["/v1/context/assemble"]["tokens_delivered"] >= 0
    for ep in day["endpoints"]:
        assert isinstance(ep["latency_p50_ms"], int)
        assert isinstance(ep["latency_p95_ms"], int)
        assert ep["latency_p95_ms"] >= ep["latency_p50_ms"]


def test_exceeding_bucket_returns_429_with_retry_after(
    tight_harness: UsageHarness,
) -> None:
    harness = tight_harness

    # Capacity 2, frozen clock (no refill): two pass, the third is 429.
    first = harness.client.post(
        "/v1/documents/resolve",
        json=_resolve_body(_AUTH_PATH),
        headers=harness.auth_header(),
    )
    assert first.status_code == 200, first.text
    second = harness.client.post(
        "/v1/documents/resolve",
        json=_resolve_body(_ERRORS_PATH),
        headers=harness.auth_header(),
    )
    assert second.status_code == 200, second.text

    third = harness.client.post(
        "/v1/documents/resolve",
        json=_resolve_body(_AUTH_PATH),
        headers=harness.auth_header(),
    )
    assert third.status_code == 429, third.text
    assert third.json()["error"]["code"] == "rate_limited"
    assert "Retry-After" in third.headers
    assert int(third.headers["Retry-After"]) >= 1
