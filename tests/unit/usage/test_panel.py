"""The local usage panel and what the usage record keeps for it (no services)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from mcp.client.client import Client
from mcp.types import Implementation
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.responses import PlainTextResponse
from starlette.types import Receive, Scope, Send

from app.api.usage import (
    REQUEST_STATE_RECORD_LOCAL,
    UsageMiddleware,
    provide_usage_session_factory,
    provide_usage_settings,
    set_usage_units,
    source_hosts,
)
from app.config import Settings
from app.logging import CLIENT_HEADER
from app.main import create_app
from app.mcp_server.client import NinaApiClient
from app.mcp_server.server import build_server
from app.store.models import UsageEvent
from app.usage.events import UsageEventRepo

LOCAL = "http://127.0.0.1:8000"


@pytest.fixture
def factory() -> Iterator[sessionmaker[Session]]:
    engine = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    UsageEvent.__table__.create(engine)
    try:
        yield sessionmaker(bind=engine)
    finally:
        engine.dispose()


def _client(
    factory: sessionmaker[Session], *, recording: bool = True, base_url: str = LOCAL
) -> TestClient:
    app = create_app()
    settings = Settings(env="dev", record_local_usage=recording, nina_version="abc123")
    app.dependency_overrides[provide_usage_settings] = lambda: settings
    app.dependency_overrides[provide_usage_session_factory] = lambda: factory
    return TestClient(app, base_url=base_url)


def _record(
    factory: sessionmaker[Session],
    second: int,
    *,
    endpoint: str = "/v1/context/assemble",
    units: dict[str, Any] | None = None,
) -> None:
    with factory() as session:
        UsageEventRepo(session).record(
            api_key_id=None,
            endpoint=endpoint,
            ts=datetime(2026, 11, 5, 10, 0, second, tzinfo=UTC),
            status_code=200,
            latency_ms=1500,
            units=units or {},
        )
        session.commit()


def test_page_is_served_with_a_strict_policy(factory: sessionmaker[Session]) -> None:
    response = _client(factory).get("/painel")
    assert response.status_code == 200
    assert "NINA no uso real" in response.text
    assert "default-src 'none'" in response.headers["content-security-policy"]


def test_data_is_newest_first_without_the_usage_reads(
    factory: sessionmaker[Session],
) -> None:
    units = {
        "output_tokens": 900,
        "page_tokens": 4000,
        "failed": 1,
        "hosts": ["docs.python.org"],
        "client": "claude-ai",
    }
    _record(factory, 1)
    _record(factory, 2, units=units)
    _record(factory, 3, endpoint="/v1/usage")
    body = _client(factory).get("/painel/dados").json()
    assert body["version"] == "abc123"
    newest, older = body["events"]
    assert newest["client"] == "claude-ai"
    assert newest["hosts"] == ["docs.python.org"]
    assert (newest["output_tokens"], newest["page_tokens"]) == (900, 4000)
    assert newest["failed"] == 1
    assert newest["ts"].startswith("2026-11-05T10:00:02")
    assert (older["client"], older["hosts"], older["failed"]) == (None, [], 0)
    assert set(newest) == {
        "ts",
        "endpoint",
        "status",
        "latency_ms",
        "output_tokens",
        "page_tokens",
        "failed",
        "hosts",
        "client",
    }


def test_malformed_units_never_break_the_panel(factory: sessionmaker[Session]) -> None:
    _record(factory, 1, units={"hosts": "x", "client": 3, "failed": "two"})
    (row,) = _client(factory).get("/painel/dados").json()["events"]
    assert (row["hosts"], row["client"], row["failed"]) == ([], None, 0)


@pytest.mark.parametrize("path", ["/painel", "/painel/dados"])
def test_panel_does_not_exist_unless_local_use_is_recorded(
    factory: sessionmaker[Session], path: str
) -> None:
    assert _client(factory, recording=False).get(path).status_code == 404


@pytest.mark.parametrize("path", ["/painel", "/painel/dados"])
def test_panel_refuses_another_host(factory: sessionmaker[Session], path: str) -> None:
    client = _client(factory, base_url="http://attacker.test")
    assert client.get(path).status_code == 404


def test_polling_the_panel_is_not_recorded(factory: sessionmaker[Session]) -> None:
    client = _client(factory)
    client.get("/painel/dados")
    with factory() as session:
        assert session.scalars(select(UsageEvent)).all() == []


def test_units_of_one_request_add_up() -> None:
    request: Any = SimpleNamespace(state=SimpleNamespace())
    set_usage_units(request, {"renders": 0, "hosts": ["a.test"]})
    set_usage_units(request, {"renders": 1})
    assert request.state.usage_units == {"renders": 1, "hosts": ["a.test"]}


def test_source_hosts_keep_only_the_host_name() -> None:
    urls = [
        "https://user:secret@Docs.Example.com/path?token=abc",
        "https://docs.example.com/other",
        "http://[bad",
    ]
    assert source_hosts(urls) == ["docs.example.com"]


def _recorded_client(factory: sessionmaker[Session], header: str) -> object:
    async def inner(scope: Scope, receive: Receive, send: Send) -> None:
        scope.setdefault("state", {})[REQUEST_STATE_RECORD_LOCAL] = True
        await PlainTextResponse("ok")(scope, receive, send)

    app = UsageMiddleware(inner, session_factory=lambda: factory)
    TestClient(app).get("/v1/x", headers={CLIENT_HEADER: header})
    with factory() as session:
        return session.scalars(select(UsageEvent)).one().units.get("client")


def test_middleware_records_the_client_name(factory: sessionmaker[Session]) -> None:
    assert _recorded_client(factory, "claude-ai") == "claude-ai"


def test_middleware_ignores_a_malformed_client_name(
    factory: sessionmaker[Session],
) -> None:
    assert _recorded_client(factory, "<script>") is None


def _api(seen: list[httpx.Request]) -> NinaApiClient:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"context": "ok"})

    return NinaApiClient(
        base_url="http://nina.test",
        api_key="k",
        transport=httpx.MockTransport(handler),
    )


async def test_mcp_tool_sends_the_name_the_client_connected_with() -> None:
    seen: list[httpx.Request] = []
    server = build_server(client=_api(seen))
    info = Implementation(name="claude-ai", version="1")
    async with Client(server, client_info=info) as session:
        await session.call_tool(
            "assemble_context", {"query": "q", "urls": ["https://a.test/"]}
        )
    assert seen[0].headers[CLIENT_HEADER] == "claude-ai"


async def test_direct_tool_call_sends_no_client_name() -> None:
    seen: list[httpx.Request] = []
    await build_server(client=_api(seen)).call_tool(
        "assemble_context", {"query": "q", "urls": ["https://a.test/"]}
    )
    assert CLIENT_HEADER not in seen[0].headers


def test_client_name_cannot_inject_a_header() -> None:
    seen: list[httpx.Request] = []
    api = _api(seen)
    api.set_caller("bad name\r\nX-Other: 1")
    api.assemble(query="q", urls=["https://a.test/"], max_tokens=1000)
    assert seen[0].headers[CLIENT_HEADER] == "badnameX-Other1"
