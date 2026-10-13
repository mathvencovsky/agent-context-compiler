"""Integration tests for Bearer API-key auth on the /v1 routes — M9 (SPEC §8).

Auth is ENFORCED here by overriding the auth settings seam to ``env=production``
(the rest of the suite runs in dev where auth is a no-op). The auth session
factory points at the real test Postgres; keys are minted directly through
:class:`ApiKeyRepo`. HTTP is never touched by these cases — resolve is reached
only to prove a valid key gets past the gate, and its transport is mocked.

Cases (SPEC §8): no header -> 401, bad Bearer -> 401, revoked key -> 401, valid
key -> 200, and GET /health -> 200 WITHOUT any auth.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.api import auth, documents
from app.config import Settings
from app.main import create_app
from app.store.blobs import BlobStore
from app.usage.keys import ApiKeyRepo
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

_PATH = "/guide"
_HTML = read_html_fixture("python_tutorial_classes.html")


def _truncate_keys(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM usage_events"))
        conn.execute(text("DELETE FROM api_keys"))


@pytest.fixture
def auth_client(
    engine: Engine,
    session_factory: sessionmaker[Session],
    blob_store: BlobStore,
    resolver: FakeResolver,
    clock: FakeClock,
    settings: Settings,
) -> Iterator[tuple[TestClient, ScriptedTransport, sessionmaker[Session]]]:
    _truncate(engine)
    _truncate_keys(engine)
    transport = ScriptedTransport()
    transport.set(_PATH, lambda: html_response(_HTML))
    app = create_app()

    prod_settings = settings.model_copy(update={"env": "production"})

    # Resolve-path seams (so a valid key can actually reach a 200).
    app.dependency_overrides[documents.provide_session_factory] = lambda: (
        session_factory
    )
    app.dependency_overrides[documents.provide_blob_store] = lambda: blob_store
    app.dependency_overrides[documents.provide_resolver] = lambda: resolver
    app.dependency_overrides[documents.provide_transport] = lambda: transport.transport
    app.dependency_overrides[documents.provide_now] = lambda: clock.now
    app.dependency_overrides[documents.provide_settings] = lambda: prod_settings
    app.dependency_overrides[documents.provide_robots_checker] = lambda: (
        _make_robots_checker(transport, resolver, prod_settings)
    )
    # Auth seams: enforce (production) against the real test DB.
    app.dependency_overrides[auth.provide_settings] = lambda: prod_settings
    app.dependency_overrides[auth.provide_auth_session_factory] = lambda: (
        session_factory
    )

    with TestClient(app) as client:
        yield client, transport, session_factory
    app.dependency_overrides.clear()
    _truncate_keys(engine)
    _truncate(engine)


def _mint_key(session_factory: sessionmaker[Session], name: str) -> str:
    session = session_factory()
    try:
        _api_key, plaintext = ApiKeyRepo(session).create(name)
        session.commit()
        return plaintext
    finally:
        session.close()


def _revoke(session_factory: sessionmaker[Session], name: str) -> None:
    session = session_factory()
    try:
        ApiKeyRepo(session).revoke_by_name(name)
        session.commit()
    finally:
        session.close()


def _resolve_body() -> dict[str, object]:
    return {"url": f"http://{FIXTURE_HOST}{_PATH}", "formats": ["markdown"]}


def test_missing_header_is_401(
    auth_client: tuple[TestClient, ScriptedTransport, sessionmaker[Session]],
) -> None:
    client, _transport, _sf = auth_client
    response = client.post("/v1/documents/resolve", json=_resolve_body())
    assert response.status_code == 401, response.text
    assert response.json()["error"]["code"] == "unauthorized"


def test_malformed_bearer_is_401(
    auth_client: tuple[TestClient, ScriptedTransport, sessionmaker[Session]],
) -> None:
    client, _transport, _sf = auth_client
    response = client.post(
        "/v1/documents/resolve",
        json=_resolve_body(),
        headers={"Authorization": "nina_whatever"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_unknown_key_is_401(
    auth_client: tuple[TestClient, ScriptedTransport, sessionmaker[Session]],
) -> None:
    client, _transport, _sf = auth_client
    response = client.post(
        "/v1/documents/resolve",
        json=_resolve_body(),
        headers={"Authorization": "Bearer nina_not_a_real_key"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_revoked_key_is_401(
    auth_client: tuple[TestClient, ScriptedTransport, sessionmaker[Session]],
) -> None:
    client, _transport, session_factory = auth_client
    plaintext = _mint_key(session_factory, "revoked")
    _revoke(session_factory, "revoked")
    response = client.post(
        "/v1/documents/resolve",
        json=_resolve_body(),
        headers={"Authorization": f"Bearer {plaintext}"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_valid_key_is_200(
    auth_client: tuple[TestClient, ScriptedTransport, sessionmaker[Session]],
) -> None:
    client, _transport, session_factory = auth_client
    plaintext = _mint_key(session_factory, "valid")
    response = client.post(
        "/v1/documents/resolve",
        json=_resolve_body(),
        headers={"Authorization": f"Bearer {plaintext}"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["document_id"].startswith("doc_")


def test_health_needs_no_auth(
    auth_client: tuple[TestClient, ScriptedTransport, sessionmaker[Session]],
) -> None:
    client, _transport, _sf = auth_client
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
