"""MCP surface of the M10b agent features: site index, version, compact."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from app.mcp_server.client import NinaApiClient
from app.mcp_server.server import build_server


def _server(seen: list[tuple[str, dict[str, Any]]]) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append((request.url.path, body))
        if request.url.path == "/v1/sites/index":
            return httpx.Response(200, json={"context": "site index: 1 of 1 links\n"})
        return httpx.Response(200, json={"context": "ctx\n"})

    client = NinaApiClient(
        base_url="http://nina.local",
        api_key="k",
        transport=httpx.MockTransport(handler),
    )
    return build_server(client=client)


async def test_resolve_url_index_format_reads_the_site_index() -> None:
    seen: list[tuple[str, dict[str, Any]]] = []
    result = await _server(seen).call_tool(
        "resolve_url", {"url": "https://docs.example.com/x", "format": "index"}
    )
    assert seen == [
        ("/v1/sites/index", {"url": "https://docs.example.com/x", "max_tokens": 2000})
    ]
    assert result.content[0].text == "site index: 1 of 1 links\n"
    assert result.structured_content is None


async def test_default_assemble_call_sends_the_same_body_as_before() -> None:
    seen: list[tuple[str, dict[str, Any]]] = []
    await _server(seen).call_tool(
        "assemble_context", {"query": "q", "urls": ["https://docs.example.com/a"]}
    )
    assert seen == [
        (
            "/v1/context/assemble",
            {
                "query": "q",
                "urls": ["https://docs.example.com/a"],
                "max_tokens": 4000,
                "response_format": "concise",
                "exclude_chunk_ids": [],
            },
        )
    ]


async def test_version_and_compact_reach_the_api() -> None:
    seen: list[tuple[str, dict[str, Any]]] = []
    await _server(seen).call_tool(
        "assemble_context",
        {
            "query": "q",
            "urls": ["https://docs.example.com/a"],
            "max_tokens": 800,
            "version": "3.12",
            "compact": True,
        },
    )
    body = seen[0][1]
    assert body["version"] == "3.12" and body["compact"] is True
    assert body["max_tokens"] == 800


async def test_tool_schemas_carry_no_generated_titles() -> None:
    tools = await _server([]).list_tools()
    for tool in tools:
        assert "title" not in tool.input_schema, tool.name
        for name, prop in tool.input_schema["properties"].items():
            assert "title" not in prop, (tool.name, name)
        assert tool.title  # the human title of the tool itself stays


def test_wire_tools_list_has_no_generated_titles() -> None:
    from mcp import Client

    async def schemas() -> list[dict[str, Any]]:
        async with Client(_server([])) as client:
            listed = await client.list_tools()
            return [t.input_schema for t in listed.tools]

    for schema in asyncio.run(schemas()):
        assert "title" not in schema
        assert all("title" not in p for p in schema["properties"].values())
