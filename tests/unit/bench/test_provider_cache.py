"""Unit tests for the persistent provider cache (no network, fake delegates).

Prove the cache's two guarantees with a counting fake delegate:

* a repeated ``(query, documents)`` rerank / repeated text embedding is served
  from the cache and does NOT re-call the delegate (quota is spent once);
* the cache persists to disk, so a fresh wrapper over the same directory sees
  the earlier results and still does not call the delegate (survives a re-run).

Nothing here touches the network — the delegate is a deterministic in-memory
stub (CLAUDE.md).
"""

from __future__ import annotations

from pathlib import Path

from bench.provider_cache import CachingEmbeddingProvider, CachingRerankProvider


class _FakeReranker:
    model = "fake-rerank"

    def __init__(self) -> None:
        self.calls = 0

    def rerank(self, query: str, documents: list[str]) -> list[float]:
        self.calls += 1
        return [float(len(d)) for d in documents]


class _FakeEmbedder:
    model = "fake-embed"
    dim = 3

    def __init__(self) -> None:
        self.calls = 0
        self.embedded: list[str] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        self.embedded.extend(texts)
        return [[float(len(t)), 0.0, 1.0] for t in texts]


def test_rerank_cache_serves_repeat_without_recalling(tmp_path: Path) -> None:
    fake = _FakeReranker()
    cache = CachingRerankProvider(fake, cache_dir=tmp_path)

    first = cache.rerank("q", ["alpha", "beta"])
    second = cache.rerank("q", ["alpha", "beta"])

    assert first == second
    assert fake.calls == 1  # second call served from cache
    assert cache.api_calls == 1
    assert cache.hits == 1


def test_rerank_cache_distinguishes_inputs(tmp_path: Path) -> None:
    fake = _FakeReranker()
    cache = CachingRerankProvider(fake, cache_dir=tmp_path)

    cache.rerank("q", ["alpha"])
    cache.rerank("q", ["alpha", "beta"])  # different documents
    cache.rerank("other", ["alpha"])  # different query

    assert fake.calls == 3
    assert cache.api_calls == 3


def test_rerank_cache_persists_across_wrappers(tmp_path: Path) -> None:
    fake1 = _FakeReranker()
    CachingRerankProvider(fake1, cache_dir=tmp_path).rerank("q", ["x", "y"])
    assert fake1.calls == 1

    # A brand-new wrapper over the same dir must reuse the persisted result.
    fake2 = _FakeReranker()
    cache2 = CachingRerankProvider(fake2, cache_dir=tmp_path)
    cache2.rerank("q", ["x", "y"])
    assert fake2.calls == 0
    assert cache2.hits == 1


def test_embed_cache_only_fetches_missing_texts(tmp_path: Path) -> None:
    fake = _FakeEmbedder()
    cache = CachingEmbeddingProvider(fake, cache_dir=tmp_path)

    cache.embed(["a", "b"])
    # "a" is cached; only "c" should go to the delegate on the second call.
    cache.embed(["a", "c"])

    assert fake.embedded == ["a", "b", "c"]
    assert cache.api_calls == 2
    assert cache.hits == 1


def test_embed_cache_persists_across_wrappers(tmp_path: Path) -> None:
    fake1 = _FakeEmbedder()
    first = CachingEmbeddingProvider(fake1, cache_dir=tmp_path).embed(["t1", "t2"])

    fake2 = _FakeEmbedder()
    cache2 = CachingEmbeddingProvider(fake2, cache_dir=tmp_path)
    second = cache2.embed(["t1", "t2"])

    assert first == second
    assert fake2.calls == 0  # fully served from disk
    assert cache2.hits == 2
