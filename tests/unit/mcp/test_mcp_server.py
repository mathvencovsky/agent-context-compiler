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
import pytest
from mcp.server.mcpserver.exceptions import ToolError

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


async def test_api_errors_reach_the_agent_with_status_and_reason() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            422,
            json={
                "error": {
                    "code": "all_sources_failed",
                    "message": "every source URL failed to resolve",
                    "details": {},
                }
            },
        )

    client = NinaApiClient(
        base_url="http://nina.local",
        api_key="test-key",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ToolError) as info:
        await build_server(client=client).call_tool(
            "assemble_context", {"query": "q", "urls": ["https://docs.example.com/a"]}
        )

    text = str(info.value)
    assert "Client error '422" in text
    assert "all_sources_failed: every source URL failed to resolve" in text


async def test_descriptions_steer_the_agent_and_stay_short() -> None:
    import json as _json

    from app.index.tokens import count_tokens

    tools = {t.name: t for t in await _build().list_tools()}
    resolve = tools["resolve_url"].description or ""
    assemble = tools["assemble_context"].description or ""
    assert "whole page" in resolve.lower() and "assemble_context" in resolve
    assert "focused question" in assemble
    assert "exclude_chunk_ids" in assemble and "budget_limited" in assemble
    for tool in tools.values():
        assert UNTRUSTED_NOTICE in (tool.description or "")
        params = tool.input_schema["properties"]
        assert all(p.get("description") for p in params.values()), tool.name
    schema = [t.model_dump(exclude_none=True) for t in tools.values()]
    assert count_tokens(_json.dumps(schema)) <= 800  # sent on every agent turn


async def test_no_tool_sends_a_second_structured_copy() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"markdown": "# Doc", "trust": "untrusted_source"}
        )

    client = NinaApiClient(
        base_url="http://nina.local",
        api_key="test-key",
        transport=httpx.MockTransport(handler),
    )
    server = build_server(client=client)
    for name, args in (
        ("resolve_url", {"url": "https://docs.example.com/a"}),
        ("get_document", {"document_id": "doc_abc"}),
    ):
        result = await server.call_tool(name, args)
        assert result.structured_content is None, name
        assert [c.type for c in result.content] == ["text"]


async def test_instructions_and_descriptions_let_a_deferred_agent_find_nina() -> None:
    # Claude Code may defer tool definitions behind tool search: the agent then
    # sees only tool names and the server instructions, and searches by keyword
    # (docs/CONTEXT_ENGINEERING.md, P2).
    from app.index.tokens import count_tokens
    from app.mcp_server.server import INSTRUCTIONS

    server = _build()
    assert server.instructions == INSTRUCTIONS
    assert "assemble_context" in INSTRUCTIONS and "documentation" in INSTRUCTIONS
    assert "untrusted" in INSTRUCTIONS.lower()
    assert count_tokens(INSTRUCTIONS) <= 100  # in every system prompt
    tools = await server.list_tools()
    for tool in tools:
        assert "documentation" in (tool.description or "").lower(), tool.name


async def test_exclude_chunk_ids_says_how_to_restore_cleared_fragments() -> None:
    tools = {t.name: t for t in await _build().list_tools()}
    param = tools["assemble_context"].input_schema["properties"]["exclude_chunk_ids"]
    assert "cleared from your context" in param["description"]


async def test_every_tool_carries_a_documentation_search_hint() -> None:
    for tool in await _build().list_tools():
        hint = (tool.meta or {}).get("anthropic/searchHint", "")
        assert "documentation" in hint, tool.name


async def test_assemble_description_names_the_tagged_format() -> None:
    tools = {t.name: t for t in await _build().list_tools()}
    description = tools["assemble_context"].description or ""
    assert '<fragment chunk="N">' in description and "<nina_notes>" in description


async def test_only_assemble_context_declares_the_result_size_ceiling() -> None:
    # Claude Code saves an MCP text result over 50,000 chars to a file unless
    # the tool declares anthropic/maxResultSizeChars (ceiling 500,000).
    tools = {t.name: t for t in await _build().list_tools()}
    assert tools["assemble_context"].meta == {
        "anthropic/searchHint": "answer questions from documentation docs",
        "anthropic/maxResultSizeChars": 500_000,
    }
    for name in ("resolve_url", "get_document"):
        assert "anthropic/maxResultSizeChars" not in (tools[name].meta or {}), name


def test_result_size_ceiling_crosses_the_wire_as_meta() -> None:
    import asyncio

    from mcp import Client

    async def metas() -> dict[str, object]:
        async with Client(_build()) as client:
            listed = await client.list_tools()
            return {
                t.name: t.model_dump(by_alias=True).get("_meta") for t in listed.tools
            }

    wire = asyncio.run(metas())
    assert wire["assemble_context"] == {
        "anthropic/searchHint": "answer questions from documentation docs",
        "anthropic/maxResultSizeChars": 500_000,
    }
    assert "anthropic/maxResultSizeChars" not in (wire["resolve_url"] or {})
