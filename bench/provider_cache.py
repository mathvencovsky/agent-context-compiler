"""Persistent, content-addressed cache for the REAL rerank/embeddings providers.

The M8 calibration (and later the Stage 2 test run) make many retrieval calls
against the paid Jina reranker and OpenAI embeddings. Two properties make those
calls cacheable:

* the rerank score of a ``(query, documents)`` pair is deterministic and does
  NOT depend on ``lambda_``/``min_value`` or the token budget — the SAME pair
  recurs across the 9 grid combinations and across Stage 2's budgets/ablations;
* an embedding of a given text is deterministic for a fixed model.

So this module wraps a real provider in a cache that is **content-addressed**
(keyed by a SHA-256 of model + inputs) and **persisted to disk** (one JSON file
per cache under ``bench/.cache/``, gitignored). A run that is interrupted and
restarted — or Stage 2 reusing Stage 1's work — serves already-computed results
from disk and NEVER re-calls the vendor for a seen input, so the Jina quota is
spent at most once per distinct ``(query, documents)`` pair.

The wrappers implement the same ``RerankProvider`` / ``EmbeddingProvider`` shape
(duck-typed), so they drop in wherever the real provider goes. They are a
benchmark utility: no app code depends on them and no test calls the network
(tests may exercise the cache logic with a fake delegate).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from app.providers.embeddings.base import EmbeddingProvider
    from app.providers.rerank.base import RerankProvider

#: Default on-disk cache directory (gitignored — generated, not source).
DEFAULT_CACHE_DIR = Path(__file__).resolve().parent / ".cache"


class _RerankLike(Protocol):
    model: str

    def rerank(self, query: str, documents: list[str]) -> list[float]: ...


class _EmbedLike(Protocol):
    model: str
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


def _digest(*parts: str) -> str:
    """SHA-256 of the parts joined by a NUL separator (stable, collision-safe)."""
    h = hashlib.sha256()
    for part in parts:
        h.update(part.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


class _JsonFileCache:
    """A tiny content-addressed JSON cache persisted next to ``path``.

    Loaded once on construction from ``path`` (the original single-file format)
    plus ``path`` with a ``.jsonl`` suffix: each new entry is appended there as
    one ``[key, value]`` line, so an interrupted run keeps every result computed
    so far and a write costs one line, not a rewrite of the whole cache (the
    embeddings file passed 290 MB, and rewriting it per vector stalled runs).
    Keys are hex digests; values are JSON-serializable.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._log = path.with_suffix(".jsonl")
        self._data: dict[str, object] = {}
        if path.exists():
            self._data = json.loads(path.read_text(encoding="utf-8"))
        if self._log.exists():
            for line in self._log.read_text(encoding="utf-8").splitlines():
                try:
                    key, value = json.loads(line)
                except ValueError:  # a crash mid-append leaves a partial last line
                    continue
                self._data[key] = value

    def get(self, key: str) -> object | None:
        return self._data.get(key)

    def put(self, key: str, value: object) -> None:
        self._data[key] = value
        self._log.parent.mkdir(parents=True, exist_ok=True)
        with self._log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps([key, value], ensure_ascii=False) + "\n")

    def __len__(self) -> int:
        return len(self._data)


class SqliteStore:
    """The same key -> JSON value cache in one SQLite file, read per key.

    Nothing is loaded up front, so a process that starts once per agent session
    (the M10 tool servers) does not parse a cache that has grown past a
    gigabyte. One lock serializes access from the API's worker threads.
    """

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS cache"
            " (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        self._lock = threading.Lock()

    def get(self, key: str) -> object | None:
        with self._lock:
            row = self._db.execute(
                "SELECT value FROM cache WHERE key = ?", (key,)
            ).fetchone()
        return None if row is None else json.loads(row[0])

    def put(self, key: str, value: object) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO cache VALUES (?, ?)", (key, json.dumps(value))
            )

    def __len__(self) -> int:
        with self._lock:
            return int(self._db.execute("SELECT count(*) FROM cache").fetchone()[0])


class CachingRerankProvider:
    """Reranker wrapper that caches ``(query, documents) -> scores`` to disk.

    The cache key is a digest of ``model`` + ``query`` + each document, so it is
    reused across grid combinations and Stage 2 budgets/ablations (the rerank of
    a task's top-30 is invariant to those). On a hit the vendor is NOT called.
    Also counts ``api_calls`` (actual vendor calls) and ``hits`` so the run can
    report how many rerank calls were really made.

    What is cached is the delegate's return verbatim, which for
    :class:`~app.providers.rerank.jina.JinaRerankProvider` is the ABSOLUTE
    ``relevance_score`` (NOT min-max normalized). This is deliberate: the
    coverage observable (``assemble._coverage``) and the frozen thresholds both
    read the absolute per-task max, so caching the absolute score keeps the
    stored data reproducible and comparable across tasks. Any min-max
    normalization the greedy selector needs is applied downstream in
    ``app.retrieve.rerank.rerank_candidates``, never baked into the cache. (An
    earlier cache stored per-call-normalized scores, which pinned every task's
    max at 1.0 and broke coverage; see DECISIONS.md 2026-10-13.)
    """

    def __init__(
        self,
        delegate: RerankProvider | _RerankLike,
        *,
        cache_dir: Path = DEFAULT_CACHE_DIR,
        store: SqliteStore | None = None,
    ) -> None:
        self._delegate = delegate
        self.model = delegate.model
        self._cache = (
            store
            if store is not None
            else _JsonFileCache(Path(cache_dir) / "rerank.json")
        )
        self.api_calls = 0
        self.hits = 0

    def rerank(self, query: str, documents: list[str]) -> list[float]:
        if not documents:
            return []
        key = _digest("rerank", self.model, query, *documents)
        cached = self._cache.get(key)
        if cached is not None:
            self.hits += 1
            return [float(x) for x in cached]  # type: ignore[union-attr]
        scores = self._delegate.rerank(query, documents)
        self.api_calls += 1
        self._cache.put(key, scores)
        return scores


class CachingEmbeddingProvider:
    """Embeddings wrapper that caches each text's vector to disk.

    Keyed by ``model`` + text, so a text embedded once is never re-embedded
    (across combinations, budgets or re-runs). Only the texts missing from the
    cache are sent to the vendor, in one batched call. Counts ``api_calls``
    (batched vendor calls) and ``hits`` (texts served from cache).
    """

    def __init__(
        self,
        delegate: EmbeddingProvider | _EmbedLike,
        *,
        cache_dir: Path = DEFAULT_CACHE_DIR,
        store: SqliteStore | None = None,
    ) -> None:
        self._delegate = delegate
        self.model = delegate.model
        self.dim = delegate.dim
        self._cache = (
            store
            if store is not None
            else _JsonFileCache(Path(cache_dir) / "embeddings.json")
        )
        self.api_calls = 0
        self.hits = 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        keys = [_digest("embed", self.model, text) for text in texts]
        results: list[list[float] | None] = [None] * len(texts)
        missing_idx: list[int] = []
        for i, key in enumerate(keys):
            cached = self._cache.get(key)
            if cached is not None:
                results[i] = [float(x) for x in cached]  # type: ignore[union-attr]
                self.hits += 1
            else:
                missing_idx.append(i)

        if missing_idx:
            fresh = self._delegate.embed([texts[i] for i in missing_idx])
            self.api_calls += 1
            for slot, vector in zip(missing_idx, fresh, strict=True):
                results[slot] = vector
                self._cache.put(keys[slot], vector)

        return [vec if vec is not None else [] for vec in results]
