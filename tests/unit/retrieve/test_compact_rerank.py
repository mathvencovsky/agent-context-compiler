"""Compaction scored by a local reranker (app/api/assemble.py::_compactor)."""

from __future__ import annotations

from app.api.assemble import _compactor, _fit_rendered
from app.config import Settings
from app.index.tokens import count_tokens
from app.retrieve.assemble import AssembleResult, Fragment, FragmentScores
from app.retrieve.hints import build_hints

KEY_SENTENCE = "Send the key in the X-Api-Key header on every request."
FILLER = " ".join(f"Unrelated sentence {i} talks about gardening." for i in range(40))
PAGE = f"## Auth\n\n{FILLER} {KEY_SENTENCE} {FILLER}"
PT_QUESTION = "Em qual cabecalho vai a credencial?"  # no word in common with PAGE
LOCAL = "http://127.0.0.1:8081"


class ScriptedReranker:
    """Fake reranker: the key sentence wins; records the texts it was asked about."""

    model = "scripted"

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.texts: list[str] = []

    def rerank(self, query: str, documents: list[str]) -> list[float]:
        if self.fail:
            raise RuntimeError("boom")
        self.texts.extend(documents)
        return [0.9 if "X-Api-Key" in d else 0.0 for d in documents]


def _settings(url: str | None) -> Settings:
    return Settings(_env_file=None, reranker_base_url=url)  # type: ignore[call-arg]


def _result() -> AssembleResult:
    fragment = Fragment(
        chunk_id="c1",
        document_id="doc_a",
        version_id="ver_a",
        source_url="https://docs.example.com/auth",
        heading_path=["Auth"],
        md=PAGE,
        tokens=count_tokens(PAGE),
        scores=FragmentScores(lexical_rank=1, vector_rank=1, rrf=0.03, rerank=0.9),
        evidence=[],
    )
    return AssembleResult(query="q", fragments=[fragment], coverage="sufficient")


def test_scorer_keeps_the_best_sentence_without_shared_words() -> None:
    result = _result()
    _compactor(result, ScriptedReranker(), _settings(LOCAL), PT_QUESTION)(200)
    assert KEY_SENTENCE in result.fragments[0].md
    assert result.compact == "pruned"
    assert "no_lexical_match" not in [h["code"] for h in build_hints(result)]


def test_scorer_runs_once_per_unit_across_fit_reruns() -> None:
    result, reranker = _result(), ScriptedReranker()
    compact = _compactor(result, reranker, _settings(LOCAL), PT_QUESTION)
    pooled = (result.fragments, result.budget_limited)
    compact(300)
    first = len(reranker.texts)
    _fit_rendered(result, pooled, compact=compact, max_tokens=120)
    assert len(reranker.texts) == first == len(set(reranker.texts))


def test_failing_reranker_falls_back_to_bm25_with_a_warning() -> None:
    result = _result()
    reranker = ScriptedReranker(fail=True)
    compact = _compactor(result, reranker, _settings(LOCAL), "api key header")
    compact(200)
    assert KEY_SENTENCE in result.fragments[0].md
    assert [w["code"] for w in result.warnings] == ["compact_rerank_failed"]
    compact(150)  # later runs stay on BM25 without a second warning
    assert len(result.warnings) == 1


def test_without_reranker_url_rerank_is_never_called() -> None:
    result, reranker = _result(), ScriptedReranker()
    _compactor(result, reranker, _settings(None), "api key header")(200)
    assert reranker.texts == [] and KEY_SENTENCE in result.fragments[0].md
