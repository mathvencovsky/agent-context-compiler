"""Offline tests for the NINA MCP server (FEAT-003, SPEC §9).

No external network: the thin :class:`NinaApiClient` is driven by an
``httpx.MockTransport`` that answers the local API routes. The tests assert the
server exposes EXACTLY the three SPEC §9 tools (each warning about untrusted
content) and that invoking ``assemble_context`` returns the mocked fragments.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from app.mcp_server.client import NinaApiClient
from app.mcp_server.server import UNTRUSTED_NOTICE, build_server

EXPECTED_TOOLS = {"resolve_url", "assemble_context", "get_document"}

_ASSEMBLE_PAYLOAD: dict[str, Any] = {
    "query": "Which header authenticates API requests?",
    "fragments": [
        {
            "chunk_id": "chk_0001",
            "document_id": "doc_abc",
            "version_id": "ver_abc",
            "source_url": "https://docs.example.com/auth",
            "heading_path": ["Authentication", "API keys"],
            "md": "Send the key in the X-Api-Key header.",
            "tokens": 9,
            "scores": {
                "lexical_rank": 1,
                "vector_rank": 1,
                "rrf": 0.032,
                "rerank": 0.91,
            },
            "evidence": [
                {
                    "block_id": "b_0001",
                    "page": None,
                    "quote": "Send the key in the X-Api-Key header.",
                }
            ],
        }
    ],
    "sources": [
        {
            "url": "https://docs.example.com/auth",
            "document_id": "doc_abc",
            "version_id": "ver_abc",
            "fetched_at": "2026-10-14T00:00:00Z",
            "status": "ok",
        }
    ],
    "token_usage": {
        "output_tokens": 9,
        "budget_tokens": 4000,
        "tokenizer": "cl100k_base (approximate)",
    },
    "coverage": "sufficient",
    "warnings": [],
    "trust": "untrusted_source",
}


_CONCISE_PAYLOAD: dict[str, Any] = {
    "context": "## https://docs.example.com/auth (doc_abc, ver_abc)\n\n"
    "### Authentication > API keys · chunk chk_0001\n"
    "Send the key in the X-Api-Key header.\n",
    "sources": _ASSEMBLE_PAYLOAD["sources"],
    "token_usage": _ASSEMBLE_PAYLOAD["token_usage"],
    "coverage": "sufficient",
    "warnings": [],
    "trust": "untrusted_source",
}


def _mock_transport() -> httpx.MockTransport:
    """A MockTransport answering the NINA API routes the client calls."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/context/assemble":
            # Echo the Bearer header back in a side channel? No — never leak the
            # key. Just assert it was sent as data the client built.
            assert request.headers["Authorization"] == "Bearer test-key"
            if json.loads(request.content)["response_format"] == "concise":
                return httpx.Response(200, json=_CONCISE_PAYLOAD)
            return httpx.Response(200, json=_ASSEMBLE_PAYLOAD)
        raise AssertionError(f"unexpected request path: {request.url.path}")

    return httpx.MockTransport(handler)


def _build() -> Any:
    client = NinaApiClient(
        base_url="http://nina.local",
        api_key="test-key",
        transport=_mock_transport(),
    )
    return build_server(client=client)


async def test_lists_exactly_three_untrusted_tools() -> None:
    server = _build()

    tools = await server.list_tools()

    names = {tool.name for tool in tools}
    assert names == EXPECTED_TOOLS
    for tool in tools:
        assert tool.description is not None
        assert UNTRUSTED_NOTICE in tool.description


async def test_assemble_context_returns_fragments_from_mocked_api() -> None:
    server = _build()

    result = await server.call_tool(
        "assemble_context",
        {
            "query": "Which header authenticates API requests?",
            "urls": ["https://docs.example.com/auth"],
            "max_tokens": 4000,
            "response_format": "detailed",
        },
    )

    assert result.is_error is False
    # The tool returns the API JSON body; the SDK serializes it to a TextContent.
    assert result.content, "expected tool content"
    text = result.content[0].text  # type: ignore[union-attr]
    body = json.loads(text)
    assert body["trust"] == "untrusted_source"
    assert body["fragments"][0]["source_url"] == "https://docs.example.com/auth"
    assert body["fragments"][0]["md"] == "Send the key in the X-Api-Key header."


async def test_assemble_context_defaults_to_concise_text_only() -> None:
    server = _build()

    result = await server.call_tool(
        "assemble_context",
        {"query": "Which header?", "urls": ["https://docs.example.com/auth"]},
    )

    assert result.is_error is False
    assert [c.type for c in result.content] == ["text"]
    assert result.content[0].text == _CONCISE_PAYLOAD["context"]  # type: ignore[union-attr]
    # no second copy of the payload as structured content
    assert result.structured_content is None
