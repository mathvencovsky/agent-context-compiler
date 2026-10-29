"""Offline tests for the frozen-corpus M10 tool servers (PREREG §3).

``full_page`` and ``strong_rag`` run on the fixture corpus with the fake
providers; ``nina`` needs Postgres and has its own integration test.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
from pathlib import Path

import httpx
import pytest
from mcp import Client, StdioServerParameters
from mcp.server.mcpserver.exceptions import ToolError

from app.config import Settings
from app.providers.rerank.fake import FakeRerankProvider
from bench.corpus_store import CorpusStore
from bench.m10_tools import (
    _CandidateLog,
    check_frozen_settings,
    corpus_transport,
    frozen_render,
    full_page_server,
    strong_rag_server,
    usage_logged,
)
from bench.page_length import page_markdown
from bench.runners.strong_rag import StrongRagRunner

_URL = "https://docs.python.org/3/library/functions.html"
_REPO = Path(__file__).resolve().parents[3]


def _call(server: object, tool: str, args: dict[str, object]) -> str:
    result = asyncio.run(server.call_tool(tool, args))  # type: ignore[attr-defined]
    assert result.is_error is False
    return str(result.content[0].text)


def test_fetch_page_returns_the_whole_frozen_page(store: CorpusStore) -> None:
    text = _call(full_page_server(store), "fetch_page", {"url": _URL})
    assert text == page_markdown(store, _URL)
    assert "absolute value" in text


def test_fetch_page_outside_the_corpus_is_not_found(store: CorpusStore) -> None:
    server = full_page_server(store)
    with pytest.raises(ToolError, match="page not found"):
        asyncio.run(server.call_tool("fetch_page", {"url": "https://example.com/x"}))


def test_search_docs_returns_strong_rag_passages(store: CorpusStore) -> None:
    server = strong_rag_server(store, StrongRagRunner())
    args = {"query": "absolute value", "urls": [_URL], "max_tokens": 500}
    text = _call(server, "search_docs", args)
    assert text.startswith(f"[URL] {_URL}\n")
    assert "absolute value" in text


def test_search_docs_without_known_pages_says_so(store: CorpusStore) -> None:
    server = strong_rag_server(store, StrongRagRunner())
    args = {"query": "abs", "urls": ["https://example.com/x"]}
    with pytest.raises(ToolError, match="no passages"):
        asyncio.run(server.call_tool("search_docs", args))


def test_tools_carry_the_untrusted_notice(store: CorpusStore) -> None:
    for server in (
        full_page_server(store),
        strong_rag_server(store, StrongRagRunner()),
    ):
        (tool,) = asyncio.run(server.list_tools())
        assert "untrusted_source" in (tool.description or "")


def test_tools_raise_claude_codes_result_size_limit(store: CorpusStore) -> None:
    # Without the annotation Claude Code saves a result over 50,000 characters
    # to a file the arms cannot read; 500,000 is the CLI's ceiling for it.
    for server in (
        full_page_server(store),
        strong_rag_server(store, StrongRagRunner()),
    ):
        (tool,) = asyncio.run(server.list_tools())
        assert tool.meta == {"anthropic/maxResultSizeChars": 500_000}


def test_corpus_transport_serves_frozen_bytes_by_host_header(
    store: CorpusStore,
) -> None:
    transport = corpus_transport(store)

    def get(path: str, host: str = "docs.python.org") -> httpx.Response:
        request = httpx.Request(
            "GET", f"https://93.184.216.34{path}", headers={"host": host}
        )
        return transport.handle_request(request)

    page = get("/3/library/functions.html")
    assert page.status_code == 200 and page.content == store.raw_bytes(_URL)
    assert page.headers["content-type"] == "text/html"
    assert "Allow: /" in get("/robots.txt").text
    assert get("/3/library/missing.html").status_code == 404
    assert get("/3/library/functions.html", host="other.org").status_code == 404


def test_frozen_render_prefers_the_frozen_rendered_html(
    tmp_path: Path, corpus_dir: Path
) -> None:
    manifest = json.loads((corpus_dir / "manifest.json").read_text(encoding="utf-8"))
    (corpus_dir / "python" / "page.rendered.html").write_text(
        "<p>rendered</p>", "utf-8"
    )
    manifest["entries"][0]["rendered_path"] = "python/page.rendered.html"
    (corpus_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    store = CorpusStore(corpus_dir)
    render = frozen_render(store)

    assert render(_URL).html == "<p>rendered</p>"
    flask = manifest["entries"][1]["url"]
    assert render(flask).html == store.raw_bytes(flask).decode()


def test_overridden_nina_settings_are_refused() -> None:
    check_frozen_settings(Settings(_env_file=None))  # type: ignore[call-arg]
    with pytest.raises(SystemExit, match="SELECTION_LAMBDA=0.3"):
        check_frozen_settings(Settings(_env_file=None, selection_lambda=0.3))  # type: ignore[call-arg]


def test_candidate_log_appends_what_the_reranker_scored(tmp_path: Path) -> None:
    log = tmp_path / "candidates.jsonl"
    reranker = _CandidateLog(FakeRerankProvider(), log)

    scores = reranker.rerank("abs value", ["abs returns the value", "other"])

    (row,) = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert row["query"] == "abs value"
    assert [c["score"] for c in row["candidates"]] == scores
    assert row["candidates"][0]["text"] == "abs returns the value"


def test_usage_log_counts_every_provider_call_cache_or_not(tmp_path: Path) -> None:
    """Front F L3: the calls a session makes, as if no bench cache existed."""
    from app.index.tokens import count_tokens
    from app.providers.embeddings.fake import FakeEmbeddingProvider

    usage = tmp_path / "usage.jsonl"
    embeddings, reranker = usage_logged(
        FakeEmbeddingProvider(), FakeRerankProvider(), usage
    )
    vectors = embeddings.embed(["abs returns", "the value"])
    scores = reranker.rerank("abs value", ["abs returns the value", "other"])
    reranker.rerank("abs value", ["abs returns the value", "other"])  # repeat

    assert len(vectors) == 2 and len(scores) == 2
    rows = [json.loads(line) for line in usage.read_text(encoding="utf-8").splitlines()]
    assert rows[0] == {
        "kind": "embed",
        "model": embeddings.model,
        "texts": 2,
        "tokens": count_tokens("abs returns") + count_tokens("the value"),
    }
    assert rows[1] == {
        "kind": "rerank",
        "model": reranker.model,
        "documents": 2,
        "query_tokens": count_tokens("abs value"),
        "document_tokens": count_tokens("abs returns the value")
        + count_tokens("other"),
    }
    assert rows[2] == rows[1]  # a repeated call is counted again
    assert embeddings.dim == FakeEmbeddingProvider().dim


def test_full_page_server_speaks_mcp_over_stdio(
    tmp_path: Path, corpus_dir: Path
) -> None:
    """The server starts the way the CLI starts it and keeps stdout clean."""
    shutil.copytree(corpus_dir, tmp_path / "bench" / "corpus")
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "bench.m10_tools", "full_page", "--data-root", str(tmp_path)],
        env={"PYTHONPATH": str(_REPO)},
        cwd=str(tmp_path),
    )

    async def session() -> tuple[list[str], str, list[object]]:
        async with Client(params) as client:
            tools = await client.list_tools()
            result = await client.call_tool("fetch_page", {"url": _URL})
            metas = [t.model_dump(by_alias=True).get("_meta") for t in tools.tools]
            return [t.name for t in tools.tools], result.content[0].text, metas  # type: ignore[union-attr]

    names, text, metas = asyncio.run(session())
    assert names == ["fetch_page"]
    assert "absolute value" in text
    # the size annotation crosses the wire as `_meta`, where Claude Code reads it
    assert metas == [{"anthropic/maxResultSizeChars": 500_000}]
