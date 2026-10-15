"""``strong_rag`` baseline over the fixture corpus: bounded, deterministic, strong.

Proves the STRONG, independent non-naive RAG baseline (sliding-window overlap
chunking, hybrid BM25+vector RRF, cross-encoder rerank of the top-30,
top-k-by-rerank-score selection under budget, ``[URL]`` provenance) respects the
token budget, is byte-identical across calls, emits the IDENTICAL provenance
header, uses OVERLAP chunking (not the blind naive chunker), and selects by
DESCENDING rerank score — NOT NINA's MMR (``app.retrieve.select.select``). A
near-duplicate pair of high-score chunks are BOTH kept when the budget allows
(the opposite of MMR's diversity penalty), which directly isolates the ``ours``
differential. Everything runs on the deterministic fakes — zero network (the
autouse ``_no_network`` guard would fail any real socket).
"""

from __future__ import annotations

import pytest

from bench.corpus_store import CorpusStore
from bench.runners import strong_rag
from bench.runners.base import available_runners, get_runner
from bench.runners.naive_rag import _chunk_text
from bench.runners.strong_rag import (
    _CHUNK_OVERLAP,
    _CHUNK_TOKENS,
    StrongRagRunner,
    _sliding_window_chunks,
)
from bench.tasks_schema import Evidence, Task

_URL = "https://docs.python.org/3/library/functions.html#abs"
_FLASK_URL = "https://flask.palletsprojects.com/en/stable/quickstart/"


def _task(urls: list[str]) -> Task:
    return Task(
        id="t1",
        category="api_reference",
        split="dev",
        urls=urls,
        question="What does abs return and what are its argument types?",
        answer_type="judge",
        expected=["the absolute value"],
        expected_evidence=[Evidence(url=urls[0], quote="absolute value")],
        author="human",
        validated=True,
    )


@pytest.fixture
def store(fixture_corpus_dir: str) -> CorpusStore:
    return CorpusStore(fixture_corpus_dir)


def test_context_within_budget(store: CorpusStore) -> None:
    runner = get_runner("strong_rag")
    result = runner.build_context(_task([_URL]), budget_tokens=50, store=store)
    assert result.context_tokens <= 50
    assert result.text  # non-empty for a real doc
    assert _URL in result.used_urls


def test_context_is_deterministic(store: CorpusStore) -> None:
    runner = get_runner("strong_rag")
    first = runner.build_context(_task([_URL]), budget_tokens=120, store=store)
    second = runner.build_context(_task([_URL]), budget_tokens=120, store=store)
    assert first == second


def test_emits_url_provenance_header(store: CorpusStore) -> None:
    runner = get_runner("strong_rag")
    result = runner.build_context(_task([_URL]), budget_tokens=200, store=store)
    assert result.text.startswith(f"[URL] {_URL}\n")


def test_relevant_url_is_used(store: CorpusStore) -> None:
    runner = get_runner("strong_rag")
    result = runner.build_context(
        _task([_URL, _FLASK_URL]), budget_tokens=200, store=store
    )
    assert _URL in result.used_urls


def test_missing_url_is_ignored(store: CorpusStore) -> None:
    runner = get_runner("strong_rag")
    result = runner.build_context(
        _task(["https://example.com/not-in-corpus"]), budget_tokens=100, store=store
    )
    assert result.text == ""
    assert result.used_urls == []


def test_sliding_window_has_overlap_unlike_blind_chunker() -> None:
    """Consecutive windows share the overlap tokens; the blind chunker does not.

    With window 400 and step 300, chunk[1] starts at token 300, so the LAST 100
    tokens of chunk[0] are the FIRST 100 tokens of chunk[1]. The blind
    ``naive_rag._chunk_text`` produces NON-overlapping chunks, proving this is
    overlap chunking, not the naive fixed chunker.
    """
    from app.index.tokens import _encoding

    encoding = _encoding()
    # A deterministic >400-token text (900 distinct tokens, each its own word).
    text = " ".join(f"token{i}" for i in range(900))

    windows = _sliding_window_chunks(text)
    assert len(windows) >= 2

    first_ids = encoding.encode(windows[0])
    second_ids = encoding.encode(windows[1])
    # chunk[0] has one full window; the overlap is its last _CHUNK_OVERLAP tokens.
    assert len(first_ids) == _CHUNK_TOKENS
    assert first_ids[-_CHUNK_OVERLAP:] == second_ids[:_CHUNK_OVERLAP]

    # The blind naive chunker produces NO overlap for the same text.
    blind = _chunk_text(text)
    blind_first = encoding.encode(blind[0])
    blind_second = encoding.encode(blind[1])
    assert blind_first[-_CHUNK_OVERLAP:] != blind_second[:_CHUNK_OVERLAP]


class _StubReranker:
    """Offline reranker that scores by an exact substring marker, descending.

    Documents containing ``"KEEP_FIRST"`` score highest, then ``"KEEP_SECOND"``,
    etc. Everything else scores 0.0. Deterministic, network-free — lets the test
    force a known rerank order independent of the fake's Jaccard overlap.
    """

    model = "stub-reranker"

    _MARKERS = ("KEEP_FIRST", "KEEP_SECOND", "KEEP_THIRD")

    def rerank(self, query: str, documents: list[str]) -> list[float]:
        scores: list[float] = []
        for doc in documents:
            score = 0.0
            for rank, marker in enumerate(self._MARKERS):
                if marker in doc:
                    score = 1.0 - rank * 0.1
                    break
            scores.append(score)
        return scores


class _OneDocStore:
    """Minimal CorpusStore stand-in exposing a single synthetic HTML doc."""

    def __init__(self, url: str, html: str) -> None:
        self._url = url
        self._html = html

    def raw_bytes(self, url: str) -> bytes:
        if url != self._url:
            raise KeyError(url)
        return self._html.encode("utf-8")

    def content_type(self, url: str) -> str:
        return "text/html"


def _marked_html(url: str, markers: list[str]) -> str:
    """Build an HTML doc whose paragraphs each carry one rerank marker + padding.

    Each paragraph is padded so trafilatura keeps it and the sliding-window
    chunker emits one chunk per marker. The markers drive the stub reranker.
    """
    filler = " ".join(f"word{i}" for i in range(60))
    paras = "".join(f"<p>{marker} {filler}</p>" for marker in markers)
    return f"<html><body><main><h1>Doc</h1>{paras}</main></body></html>"


def test_selection_follows_descending_rerank_score_not_mmr() -> None:
    """Top-k selection is by DESCENDING rerank score and does NOT apply MMR.

    The stub reranker forces KEEP_FIRST > KEEP_SECOND > KEEP_THIRD. With a budget
    that fits all three, the emitted order (corpus order) and used chunks follow
    the rerank ranking — and the two highest-scored, near-duplicate chunks are
    BOTH kept, which NINA's MMR selection would penalize. This isolates the
    differential: the baseline must not depend on ``app.retrieve.select.select``.
    """
    url = "https://example.com/marked"
    # Two near-duplicate top chunks (KEEP_FIRST / KEEP_SECOND share most tokens)
    # plus a lower one; MMR would drop a near-duplicate, top-k keeps both.
    html = _marked_html(url, ["KEEP_FIRST", "KEEP_SECOND", "KEEP_THIRD"])
    store = _OneDocStore(url, html)

    runner = StrongRagRunner(reranker=_StubReranker())
    task = Task(
        id="t_rank",
        category="guide",
        split="dev",
        urls=[url],
        question="which chunk should rank first",
        answer_type="judge",
        expected=["x"],
        expected_evidence=[Evidence(url=url, quote="x")],
        author="human",
        validated=True,
    )

    result = runner.build_context(task, budget_tokens=2000, store=store)  # type: ignore[arg-type]

    # All three markers survive (no MMR diversity penalty dropped a duplicate).
    assert "KEEP_FIRST" in result.text
    assert "KEEP_SECOND" in result.text
    assert "KEEP_THIRD" in result.text

    # Under a budget that fits only ONE chunk, the SINGLE highest-reranked chunk
    # (KEEP_FIRST) is the one kept — selection is by descending rerank score.
    tight = runner.build_context(task, budget_tokens=80, store=store)  # type: ignore[arg-type]
    assert "KEEP_FIRST" in tight.text


def test_does_not_import_mmr_select() -> None:
    """``strong_rag`` must not use NINA's MMR selector (the isolated variable)."""
    assert not hasattr(strong_rag, "select")


def test_runner_registered() -> None:
    assert "strong_rag" in available_runners()
    assert isinstance(get_runner("strong_rag"), StrongRagRunner)
