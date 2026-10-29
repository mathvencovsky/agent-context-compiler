"""MCP hardening (audit B2 and the MCP HTTP transport review, 2026-10-28)."""

from __future__ import annotations

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError
from starlette.testclient import TestClient

from app.mcp_server.__main__ import HTTP_HOST, http_transport_security
from app.mcp_server.client import NinaApiClient
from app.mcp_server.server import build_server


def _client(seen: list[str]) -> NinaApiClient:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.raw_path.decode())
        return httpx.Response(200, json={"markdown": "# Doc"})

    return NinaApiClient(
        base_url="http://nina.local",
        api_key="test-key",
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.parametrize(
    ("document_id", "version_id"),
    [
        ("../usage", None),
        ("..%2Fusage", None),
        ("doc_abc/../../usage", None),
        ("x?formats=markdown#", None),
        ("doc_abc", "../../v1/usage"),
        ("doc_abc", "ver_abc&x=1"),
    ],
)
async def test_get_document_refuses_ids_that_could_change_the_path(
    document_id: str, version_id: str | None
) -> None:
    seen: list[str] = []
    server = build_server(client=_client(seen))
    args: dict[str, str] = {"document_id": document_id}
    if version_id is not None:
        args["version_id"] = version_id
    with pytest.raises(ToolError, match="must look like"):
        await server.call_tool("get_document", args)
    assert seen == []  # no request left the MCP server


async def test_get_document_accepts_real_ids() -> None:
    seen: list[str] = []
    server = build_server(client=_client(seen))
    result = await server.call_tool(
        "get_document", {"document_id": "doc_0123abcd", "version_id": "ver_ff00"}
    )
    assert not result.is_error
    assert seen == ["/v1/documents/doc_0123abcd?version_id=ver_ff00"]


def _http_app() -> TestClient:
    server = build_server(client=_client([]))
    app = server.streamable_http_app(
        host=HTTP_HOST, transport_security=http_transport_security()
    )
    return TestClient(app, base_url="http://127.0.0.1:8000")


_INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "t", "version": "0"},
    },
}
_HEADERS = {"Accept": "application/json, text/event-stream"}


def test_http_transport_is_loopback_only() -> None:
    assert HTTP_HOST == "127.0.0.1"


def test_http_transport_rejects_a_foreign_origin() -> None:
    with _http_app() as client:
        response = client.post(
            "/mcp",
            json=_INIT,
            headers={**_HEADERS, "Origin": "http://evil.example"},
        )
    assert response.status_code == 403


def test_http_transport_rejects_a_rebound_host() -> None:
    with _http_app() as client:
        response = client.post(
            "/mcp", json=_INIT, headers={**_HEADERS, "Host": "evil.example:8000"}
        )
    assert response.status_code in (403, 421)


def test_http_transport_accepts_a_loopback_origin() -> None:
    with _http_app() as client:
        response = client.post(
            "/mcp",
            json=_INIT,
            headers={**_HEADERS, "Origin": "http://127.0.0.1:8000"},
        )
    assert response.status_code == 200
