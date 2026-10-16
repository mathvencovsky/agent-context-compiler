"""The ``nina`` arm's tool server over the real API (M10, PREREG §3).

The shipped MCP server runs on the real resolve, assemble and get-document
endpoints, fed by the bench-only fetcher over a one-page frozen corpus.
Postgres and SeaweedFS are real, the providers are the fakes, and nothing
leaves the machine.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from sqlalchemy.engine import Engine

from app.api import documents
from app.providers.embeddings.fake import FakeEmbeddingProvider
from app.providers.rerank.fake import FakeRerankProvider
from bench.corpus_store import CorpusStore
from bench.m10_tools import nina_server
from tests.integration.conftest import _truncate, read_html_fixture

pytestmark = pytest.mark.integration

_URL = "https://docs.python.org/3/library/functions.html"


@pytest.fixture
def server(
    tmp_path: Path, engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> Iterator[MCPServer]:
    body = read_html_fixture("python_stdlib_functions.html")
    digest = hashlib.sha256(body).hexdigest()
    corpus = tmp_path / "corpus"
    (corpus / "python").mkdir(parents=True)
    (corpus / "python" / f"{digest}.html").write_bytes(body)
    entry = {
        "site": "Python",
        "site_key": "python",
        "rich_categories": ["api_reference"],
        "url": _URL,
        "final_url": _URL,
        "status": "ok",
        "reason": None,
        "content_type": "text/html",
        "content_hash": digest,
        "fetched_at": "2026-10-15T00:00:00+00:00",
        "raw_path": f"python/{digest}.html",
        "rendered_path": None,
    }
    manifest = {"created_at": "2026-10-15T00:00:00+00:00", "entries": [entry]}
    (corpus / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    # nina_server swaps the renderer; monkeypatch puts the real one back.
    monkeypatch.setattr(documents, "render_url", documents.render_url)
    yield nina_server(
        CorpusStore(corpus),
        embeddings=FakeEmbeddingProvider(),
        reranker=FakeRerankProvider(),
        log=tmp_path / "candidates.jsonl",
    )
    _truncate(engine)


def _call(server: MCPServer, tool: str, args: dict[str, Any]) -> dict[str, Any]:
    result = asyncio.run(server.call_tool(tool, args))
    assert result.is_error is False  # type: ignore[union-attr]
    body: dict[str, Any] = json.loads(result.content[0].text)  # type: ignore[union-attr]
    return body


def test_assemble_context_serves_the_frozen_page_and_logs_candidates(
    server: MCPServer, tmp_path: Path
) -> None:
    query = "Return the absolute value of a number"
    body = _call(
        server,
        "assemble_context",
        {
            "query": query,
            "urls": [_URL],
            "max_tokens": 2000,
            "response_format": "detailed",
        },
    )

    assert body["trust"] == "untrusted_source"
    assert [s["status"] for s in body["sources"]] == ["ok"]
    assert body["fragments"]
    assert {f["source_url"] for f in body["fragments"]} == {_URL}
    lines = (tmp_path / "candidates.jsonl").read_text(encoding="utf-8").splitlines()
    scored = json.loads(lines[0])
    assert scored["query"] == query and scored["candidates"]


def test_resolve_then_get_document(server: MCPServer) -> None:
    resolved = _call(server, "resolve_url", {"url": _URL})
    assert "absolute value" in resolved["markdown"]

    stored = _call(server, "get_document", {"document_id": resolved["document_id"]})
    assert stored["version_id"] == resolved["version_id"]


def test_a_page_outside_the_corpus_fails_as_in_production(server: MCPServer) -> None:
    missing = "https://docs.python.org/3/library/not-frozen.html"
    with pytest.raises(ToolError) as info:
        asyncio.run(
            server.call_tool(
                "assemble_context",
                {"query": "abs", "urls": [missing], "response_format": "detailed"},
            )
        )
    assert "422" in str(info.value.__cause__)  # every source failed (SPEC §8)
