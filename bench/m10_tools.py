"""Documentation tools of the frozen-corpus M10 arms, as stdio MCP servers (PREREG §3).

:mod:`bench.m10_agent` starts one per session. ``--data-root`` is the checkout
holding the data: its ``.env``, ``bench/corpus`` and ``bench/.cache``::

    python -m bench.m10_tools {full_page|strong_rag|nina} --data-root DIR \
        [--log FILE] [--usage FILE]

* ``full_page``: ``fetch_page(url)``, the whole frozen page as trafilatura
  Markdown (:func:`bench.page_length.page_markdown`, SPEC §10.3).
* ``strong_rag``: ``search_docs(query, urls, max_tokens=4000)``, the M8
  ``strong_rag`` pipeline over the frozen pages.
* ``nina``: the shipped server (:func:`app.mcp_server.server.build_server`), its
  stock :class:`~app.mcp_server.client.NinaApiClient` reaching the real API
  in-process. Only the fetch side is swapped: the fetcher and the browser
  render serve the frozen snapshot, robots.txt allows everything (the cited
  pages were checked by hand, DECISIONS 2026-10-15) and every host resolves to
  one public address, so nothing leaves the machine. The API keeps its data in
  its own database, :data:`NINA_DB`, and refuses settings that differ from the
  frozen ``app/`` defaults.

Embeddings and reranker are the real providers behind SQLite caches in
``bench/.cache/m10/`` (the JSON caches there are too large to load once per
session). Each candidate set NINA's reranker scores is appended to ``--log``
for the PREREG §8 failure causes; the agent never sees it. ``--usage`` logs the
size of every embedding and rerank call, cache hits included, for the full CPT
(:func:`usage_logged`). A URL outside the
frozen corpus is not found, in every arm. An internal failure reaches the agent
as a tool error starting with :data:`INFRA_ERROR` (``nina``: an HTTP 500), which
the runner counts as an infrastructure failure (PREREG §10).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from app.config import Settings, get_settings
from app.fetch.render import RenderResult
from app.mcp_server.server import READ_ONLY, UNTRUSTED_NOTICE, build_server
from app.providers.embeddings.base import EmbeddingProvider
from app.providers.rerank.base import RerankProvider
from bench.corpus_store import CorpusStore
from bench.page_length import page_markdown
from bench.runners.strong_rag import StrongRagRunner
from bench.tasks_schema import Task

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[1]
#: The NINA API's own database, so test truncation and dev data never touch it.
NINA_DB = "nina_m10"
#: Relative to the data root.
CORPUS_DIR = Path("bench/corpus")
CACHE_DIR = Path("bench/.cache/m10")
#: Prefix of a tool error that is the harness's fault, not the agent's.
INFRA_ERROR = "infrastructure error"
#: Every host resolves here; the mock transport never connects to it.
_PUBLIC_IP = "93.184.216.34"
#: NINA settings that must keep the frozen ``app/`` default (PREREG §10).
FROZEN_SETTINGS = (
    "env",
    "selection_lambda",
    "min_value",
    "coverage_low",
    "coverage_high",
    "rrf_k",
    "lexical_weight",
    "embedding_model",
    "embedding_dim",
    "reranker_model",
    "render_min_text_chars",
    # Markdown sources (SPEC §3.5, M10b): the frozen corpus holds only the
    # cited pages, so a Markdown alternate would 404 and add a warning, and an
    # Accept preference would be ignored by the corpus transport. Both stay off.
    "markdown_alternate",
    "markdown_alternate_min_overlap",
    "fetch_accept_markdown",
)


#: Claude Code saves any MCP text result over 50,000 characters to a file,
#: whatever ``MAX_MCP_OUTPUT_TOKENS`` says, unless the tool declares this
#: ``_meta`` key; 500,000 is the CLI's hard ceiling for it (Claude Code MCP
#: docs, "MCP output limits"; read in CLI 2.1.301). The arms have no tool to
#: read such a file, so without it a long page would never reach the agent.
MAX_RESULT_CHARS = 500_000
RESULT_META = {"anthropic/maxResultSizeChars": MAX_RESULT_CHARS}


def full_page_server(store: CorpusStore) -> MCPServer:
    """``fetch_page(url)``: the whole frozen page as Markdown."""
    server: MCPServer[None] = MCPServer(name="docs")

    @server.tool(
        description="Return the whole documentation page at `url` as Markdown. "
        + UNTRUSTED_NOTICE,
        title="Read a whole documentation page",
        annotations=READ_ONLY,
        meta=RESULT_META,
    )
    def fetch_page(url: str) -> str:
        try:
            return page_markdown(store, url)
        except (KeyError, ValueError):
            raise ToolError(f"page not found: {url}") from None
        except Exception as exc:
            raise ToolError(f"{INFRA_ERROR}: {type(exc).__name__}") from exc

    return server


def strong_rag_server(store: CorpusStore, runner: StrongRagRunner) -> MCPServer:
    """``search_docs(query, urls, max_tokens=4000)``: the M8 strong_rag context."""
    server: MCPServer[None] = MCPServer(name="docs")

    @server.tool(
        description="Search the documentation pages at `urls` for `query` and "
        "return the most relevant passages, at most `max_tokens` tokens, each "
        "headed by its page URL. " + UNTRUSTED_NOTICE,
        title="Search documentation pages",
        annotations=READ_ONLY,
        meta=RESULT_META,
    )
    def search_docs(query: str, urls: list[str], max_tokens: int = 4000) -> str:
        task = Task(
            id="search_docs",
            category="guide",
            split="test",
            urls=urls,
            question=query,
            answer_type="judge",
            expected=[""],
            expected_evidence=[],
            author="human",
            validated=True,
        )
        try:
            text = runner.build_context(
                task, budget_tokens=max_tokens, store=store
            ).text
        except Exception as exc:
            raise ToolError(f"{INFRA_ERROR}: {type(exc).__name__}") from exc
        if not text:
            raise ToolError("no passages found in the given pages")
        return text

    return server


def nina_server(
    store: CorpusStore,
    *,
    embeddings: EmbeddingProvider,
    reranker: RerankProvider,
    log: Path | None = None,
) -> MCPServer:
    """The shipped NINA server over the real API, fed by the frozen corpus."""
    from fastapi.testclient import TestClient

    from app.api import assemble, documents
    from app.fetch.client import fetch
    from app.fetch.robots import RobotsChecker
    from app.main import create_app
    from app.mcp_server.client import NinaApiClient

    settings = get_settings()
    transport = corpus_transport(store)
    resolver = _PublicResolver()
    robots = RobotsChecker(
        fetch_fn=lambda url: fetch(
            url, resolver=resolver, transport=transport, settings=settings
        ),
        settings=settings,
    )
    scorer = _CandidateLog(reranker, log) if log is not None else reranker
    # The product renders script-only pages in a browser; here that fetch also
    # reads the snapshot (its rendered HTML when one was frozen).
    documents.render_url = frozen_render(store)  # type: ignore[assignment]
    api = create_app()
    api.dependency_overrides.update(
        {
            documents.provide_transport: lambda: transport,
            documents.provide_resolver: lambda: resolver,
            documents.provide_robots_checker: lambda: robots,
            assemble.provide_embeddings: lambda: embeddings,
            assemble.provide_reranker: lambda: scorer,
        }
    )
    # A crash inside the API becomes its 500 response, as in production.
    app_client = TestClient(api, raise_server_exceptions=False)
    client = NinaApiClient(
        base_url="http://nina.bench",
        api_key="bench",
        transport=_AppTransport(app_client),
    )
    return build_server(client=client)


def corpus_transport(store: CorpusStore) -> httpx.MockTransport:
    """The bench-only fetcher: frozen bytes, an allow-all robots.txt, else 404."""

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        # The guarded client sends to the validated IP, with the host in Host.
        path = request.url.raw_path.decode("ascii")
        url = f"{request.url.scheme}://{request.headers['host']}{path}"
        try:
            body = store.raw_bytes(url)
        except (KeyError, ValueError):
            return httpx.Response(404)
        return httpx.Response(
            200, content=body, headers={"content-type": store.content_type(url)}
        )

    return httpx.MockTransport(handle)


def frozen_render(store: CorpusStore) -> Callable[..., RenderResult]:
    """The bench's browser: the frozen rendered HTML, else the raw page."""

    def render(url: str, **_kwargs: object) -> RenderResult:
        html = store.rendered_html(url)
        if html is None:
            html = store.raw_bytes(url).decode("utf-8", errors="replace")
        return RenderResult(final_url=url, html=html, status=200)

    return render


def check_frozen_settings(settings: Settings) -> None:
    """Refuse NINA settings that differ from the frozen ``app/`` defaults."""
    changed = [
        f"{name.upper()}={getattr(settings, name)!r}"
        for name in FROZEN_SETTINGS
        if getattr(settings, name) != Settings.model_fields[name].default
    ]
    if changed:
        raise SystemExit(
            "the environment or .env overrides frozen NINA settings: "
            + ", ".join(changed)
            + ". Remove them before an M10 run (PREREG §10)."
        )


def real_providers(cache_dir: Path) -> tuple[EmbeddingProvider, RerankProvider]:
    """OpenAI embeddings and the Jina reranker, paid only on a cache miss."""
    from app.providers.embeddings.openai import OpenAIEmbeddingProvider
    from app.providers.rerank.jina import JinaRerankProvider
    from bench.provider_cache import (
        CachingEmbeddingProvider,
        CachingRerankProvider,
        SqliteStore,
    )

    embeddings = CachingEmbeddingProvider(
        OpenAIEmbeddingProvider(), store=SqliteStore(cache_dir / "embeddings.sqlite")
    )
    reranker = CachingRerankProvider(
        JinaRerankProvider(), store=SqliteStore(cache_dir / "rerank.sqlite")
    )
    return embeddings, reranker


#: Tables whose rows mean NINA_DB is not empty: what resolve and assemble cache.
_NINA_DATA_TABLES = ("sources", "document_versions", "chunks")


class NinaDatabase(Protocol):
    """The operations :func:`prepare_nina_database` needs on :data:`NINA_DB`."""

    def exists(self) -> bool: ...
    def has_data(self) -> bool: ...
    def create(self) -> None: ...
    def drop(self) -> None: ...
    def migrate(self) -> None: ...


class PostgresNinaDatabase:
    """:data:`NINA_DB` on the Postgres server of the ``.env`` settings.

    ``name`` exists for the integration test, which must never touch the real
    :data:`NINA_DB`.
    """

    def __init__(
        self, settings: Settings | None = None, *, name: str = NINA_DB
    ) -> None:
        self._s = settings if settings is not None else get_settings()
        self._name = name
        if self._s.postgres_db == name:
            # Never let a reset drop the database the dev API or the tests use.
            raise SystemExit(
                f"POSTGRES_DB is {name!r}; the benchmark database must be "
                "separate from the configured one"
            )

    def _connect(self, dbname: str) -> Any:
        import psycopg

        return psycopg.connect(
            host=self._s.postgres_host,
            port=self._s.postgres_port,
            user=self._s.postgres_user,
            password=self._s.postgres_password,
            dbname=dbname,
            autocommit=True,
        )

    def exists(self) -> bool:
        with self._connect("postgres") as conn:
            query = "SELECT 1 FROM pg_database WHERE datname = %s"
            return conn.execute(query, (self._name,)).fetchone() is not None

    def has_data(self) -> bool:
        from psycopg import sql

        with self._connect(self._name) as conn:
            for table in _NINA_DATA_TABLES:
                if conn.execute("SELECT to_regclass(%s)", (table,)).fetchone()[0]:
                    stmt = sql.SQL("SELECT EXISTS (SELECT 1 FROM {})").format(
                        sql.Identifier(table)
                    )
                    if conn.execute(stmt).fetchone()[0]:
                        return True
        return False

    def create(self) -> None:
        from psycopg import sql

        with self._connect("postgres") as conn:
            conn.execute(
                sql.SQL("CREATE DATABASE {}").format(sql.Identifier(self._name))
            )

    def drop(self) -> None:
        from psycopg import sql

        with self._connect("postgres") as conn:
            conn.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                    sql.Identifier(self._name)
                )
            )

    def migrate(self) -> None:
        env = {
            **os.environ,
            "POSTGRES_HOST": self._s.postgres_host,
            "POSTGRES_PORT": str(self._s.postgres_port),
            "POSTGRES_USER": self._s.postgres_user,
            "POSTGRES_PASSWORD": self._s.postgres_password,
            "POSTGRES_DB": self._name,
        }
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=REPO_ROOT,
            env=env,
            check=True,
        )


def prepare_nina_database(
    *, resuming: bool, reset: bool, db: NinaDatabase | None = None
) -> str:
    """Make :data:`NINA_DB` ready for a run and return what was done.

    PREREG Amendment 17: the NINA database starts EMPTY in a run, so that first
    indexing (embeddings, rerank) is counted in the full CPT. So:

    * a new run (``resuming`` false: no ``results.csv`` yet) gets an empty
      database: ``"created"`` if it was missing, ``"empty"`` if it exists with
      no rows in ``sources``/``document_versions``/``chunks``; a database that
      holds rows is refused unless ``reset`` (``--reset-nina-db``), which drops
      and recreates it (``"reset"``). Dropping is never implicit;
    * a resumed run (``results.csv`` exists) keeps the database as it is
      (``"kept"``; ``"created"`` if an earlier invocation ran without the
      ``nina`` arm); ``reset`` is refused, since it would count first indexing
      twice within one run.

    The database is migrated to head in every case.
    """
    db = db if db is not None else PostgresNinaDatabase()
    if resuming:
        if reset:
            raise SystemExit(
                "--reset-nina-db with an existing results.csv: a resumed run keeps "
                f"{NINA_DB} (PREREG Amendment 17); move results.csv away to start "
                "a new run"
            )
        action = "kept" if db.exists() else "created"
        if action == "created":
            db.create()
    elif not db.exists():
        db.create()
        action = "created"
    elif not db.has_data():
        action = "empty"
    elif reset:
        db.drop()
        db.create()
        action = "reset"
    else:
        raise SystemExit(
            f"{NINA_DB} already holds documents but no results.csv exists: a new "
            "run must start with an empty NINA database (PREREG Amendment 17). "
            "Pass --reset-nina-db to drop and recreate it."
        )
    db.migrate()
    return action


class _PublicResolver:
    """Resolves every host to one public address (no DNS lookup)."""

    def resolve(self, host: str) -> list[str]:
        return [_PUBLIC_IP]


class _CandidateLog:
    """Reranker that appends each scored candidate set to ``path`` (PREREG §8)."""

    def __init__(self, delegate: RerankProvider, path: Path) -> None:
        self.model = delegate.model
        self._delegate = delegate
        self._path = path

    def rerank(self, query: str, documents: list[str]) -> list[float]:
        scores = self._delegate.rerank(query, documents)
        row = {
            "query": query,
            "candidates": [
                {"text": text, "score": score}
                for text, score in zip(documents, scores, strict=True)
            ],
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        return scores


def _append_usage(path: Path, row: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


class _EmbedUsage:
    """Embeddings that append each call's billed size to ``path``."""

    def __init__(self, delegate: EmbeddingProvider, path: Path) -> None:
        self.model = delegate.model
        self.dim = delegate.dim
        self._delegate = delegate
        self._path = path

    def embed(self, texts: list[str]) -> list[list[float]]:
        from app.index.tokens import count_tokens

        vectors = self._delegate.embed(texts)
        tokens = sum(count_tokens(t) for t in texts)
        _append_usage(
            self._path,
            {
                "kind": "embed",
                "model": self.model,
                "texts": len(texts),
                "tokens": tokens,
            },
        )
        return vectors


class _RerankUsage:
    """Reranker that appends each call's billed size to ``path``."""

    def __init__(self, delegate: RerankProvider, path: Path) -> None:
        self.model = delegate.model
        self._delegate = delegate
        self._path = path

    def rerank(self, query: str, documents: list[str]) -> list[float]:
        from app.index.tokens import count_tokens

        scores = self._delegate.rerank(query, documents)
        _append_usage(
            self._path,
            {
                "kind": "rerank",
                "model": self.model,
                "documents": len(documents),
                "query_tokens": count_tokens(query),
                "document_tokens": sum(count_tokens(d) for d in documents),
            },
        )
        return scores


def usage_logged(
    embeddings: EmbeddingProvider, reranker: RerankProvider, path: Path
) -> tuple[EmbeddingProvider, RerankProvider]:
    """Providers that log every call to ``path`` (research front F, L3).

    They wrap the caching providers, so a cache hit is logged like a paid call:
    the log is the usage of a customer without the bench's cache, which
    :mod:`bench.full_cpt` prices as the "full CPT". Tokens are ``cl100k_base``,
    the encoding of ``text-embedding-3-small``; for the Jina reranker, whose
    tokenizer differs, they are an estimate. NINA's own document store still
    applies: a page already indexed in :data:`NINA_DB` is not embedded again,
    as for a customer of the hosted product.
    """
    return _EmbedUsage(embeddings, path), _RerankUsage(reranker, path)


class _AppTransport(httpx.BaseTransport):
    """Sends the API client's requests to the in-process app (no socket)."""

    def __init__(self, app_client: TestClient) -> None:
        self._app = app_client

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        response = self._app.request(
            request.method,
            str(request.url),
            headers=request.headers,
            content=request.read(),
        )
        return httpx.Response(
            response.status_code,
            headers=response.headers,
            content=response.content,
            request=request,
        )


def main(argv: list[str] | None = None) -> None:  # pragma: no cover - a subprocess
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("arm", choices=("full_page", "strong_rag", "nina"))
    parser.add_argument("--data-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--log", type=Path, help="nina: scored candidates file")
    parser.add_argument("--usage", type=Path, help="provider calls file (full CPT)")
    args = parser.parse_args(argv)

    os.chdir(args.data_root)
    store = CorpusStore(CORPUS_DIR)
    if args.arm == "full_page":
        server = full_page_server(store)
    else:
        if args.arm == "nina":
            os.environ["POSTGRES_DB"] = NINA_DB
            get_settings.cache_clear()
            check_frozen_settings(get_settings())
        embeddings, reranker = real_providers(CACHE_DIR)
        if args.usage is not None:
            embeddings, reranker = usage_logged(embeddings, reranker, args.usage)
        if args.arm == "strong_rag":
            runner = StrongRagRunner(embeddings=embeddings, reranker=reranker)
            server = strong_rag_server(store, runner)
        else:
            server = nina_server(
                store, embeddings=embeddings, reranker=reranker, log=args.log
            )
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
