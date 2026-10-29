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
from app.config import get_settings
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


# --------------------------------------------------------------------------- #
# Markdown alternates (SPEC §3.5, M10b): snapshot runs never fetch them
# --------------------------------------------------------------------------- #
_ALT_PAGE = "https://docs.example.com/guide/tokens.html"
_ALT_HTML = b"""<!doctype html><html><head><title>Tokens</title>
<link rel="alternate" type="text/markdown" href="/guide/tokens.md">
</head><body><main><h1>Tokens</h1>
<p>Tokens expire after one hour unless they are refreshed with the refresh
endpoint. A refreshed token keeps the scopes of the original token and the
same audience, and the refresh token itself is single use.</p>
<h2>Refresh</h2>
<p>Send the refresh token to the refresh endpoint to obtain a new access
token before the old one expires.</p>
</main></body></html>"""


class _RecordingStore(CorpusStore):
    """The frozen corpus, recording every URL the fetcher asks it for."""

    def __init__(self, corpus_dir: Path) -> None:
        super().__init__(corpus_dir)
        self.requested: list[str] = []

    def raw_bytes(self, url: str) -> bytes:
        self.requested.append(url)
        return super().raw_bytes(url)


def _alternate_corpus(tmp_path: Path) -> _RecordingStore:
    digest = hashlib.sha256(_ALT_HTML).hexdigest()
    corpus = tmp_path / "corpus"
    (corpus / "example").mkdir(parents=True)
    (corpus / "example" / f"{digest}.html").write_bytes(_ALT_HTML)
    entry = {
        "site": "Example",
        "site_key": "example",
        "rich_categories": ["guide"],
        "url": _ALT_PAGE,
        "final_url": _ALT_PAGE,
        "status": "ok",
        "reason": None,
        "content_type": "text/html",
        "content_hash": digest,
        "fetched_at": "2026-10-15T00:00:00+00:00",
        "raw_path": f"example/{digest}.html",
        "rendered_path": None,
    }
    manifest = {"created_at": "2026-10-15T00:00:00+00:00", "entries": [entry]}
    (corpus / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return _RecordingStore(corpus)


def _resolve_frozen_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[dict[str, Any], list[str]]:
    store = _alternate_corpus(tmp_path)
    monkeypatch.setattr(documents, "render_url", documents.render_url)
    server = nina_server(
        store,
        embeddings=FakeEmbeddingProvider(),
        reranker=FakeRerankProvider(),
    )
    return _call(server, "resolve_url", {"url": _ALT_PAGE}), store.requested


@pytest.fixture
def _fresh_settings(engine: Engine) -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
    _truncate(engine)


@pytest.mark.usefixtures("_fresh_settings")
def test_snapshot_run_never_fetches_a_declared_markdown_alternate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The frozen defaults (check_frozen_settings enforces them): the page is
    # extracted from its frozen HTML exactly as before M10b.
    monkeypatch.delenv("MARKDOWN_ALTERNATE", raising=False)
    monkeypatch.delenv("FETCH_ACCEPT_MARKDOWN", raising=False)
    resolved, requested = _resolve_frozen_page(tmp_path, monkeypatch)

    assert requested == [_ALT_PAGE]
    assert "alternate_url" not in resolved["source"]
    assert "alternate_content_hash" not in resolved["source"]
    assert not [w for w in resolved["warnings"] if "alternate" in w["code"]]
    assert "Tokens expire after one hour" in resolved["markdown"]


@pytest.mark.usefixtures("_fresh_settings")
def test_markdown_alternate_on_would_reach_the_corpus(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Control for the test above: with the setting on, the alternate IS asked
    # of the corpus (absent there: 404), the HTML is kept with a warning. This
    # is why check_frozen_settings refuses MARKDOWN_ALTERNATE in M10 runs.
    monkeypatch.setenv("MARKDOWN_ALTERNATE", "true")
    resolved, requested = _resolve_frozen_page(tmp_path, monkeypatch)

    assert requested == [_ALT_PAGE, "https://docs.example.com/guide/tokens.md"]
    codes = [w["code"] for w in resolved["warnings"]]
    assert "markdown_alternate_failed" in codes
    assert "alternate_url" not in resolved["source"]
