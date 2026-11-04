"""Low-budget mode (``compact``): verbatim units, atomic code/tables (L7)."""

from __future__ import annotations

from app.index.tokens import count_tokens
from app.retrieve.assemble import AssembleResult, Fragment, FragmentScores
from app.retrieve.compact import (
    ELISION,
    MAX_POOL_TOKENS,
    compact_pieces,
    compact_result,
    pool_budget,
)

FILLER = " ".join(
    f"Unrelated sentence number {i} talks about gardening and weather."
    for i in range(30)
)
AUTH = (
    "## Authentication\n\n"
    f"{FILLER} Send the key in the X-Api-Key header on every request. {FILLER}\n\n"
    "```python\nclient = Client(api_key=KEY)\nclient.get('/v1')\n```\n\n"
    "| header | meaning |\n|---|---|\n| X-Api-Key | your key |\n"
)
OTHER = f"## Rate limits\n\n{FILLER}\n\nThe X-Api-Key header also sets the rate limit."


def _runs(text: str) -> list[str]:
    return [p for p in text.split(f"\n{ELISION}\n") if p and p != ELISION]


def test_pool_budget_is_four_times_capped() -> None:
    assert pool_budget(1000) == 4000
    assert pool_budget(16_000) == MAX_POOL_TOKENS


def test_keeps_matching_sentences_verbatim_within_budget() -> None:
    query = "Which header carries the API key?"
    out = compact_pieces(query, [AUTH, OTHER], [0.9, 0.5], 300)
    assert out.pruned
    kept = [t for t in out.texts if t is not None]
    assert sum(count_tokens(t) for t in kept) <= 300
    joined = "\n".join(kept)
    assert "Send the key in the X-Api-Key header on every request." in joined
    assert "The X-Api-Key header also sets the rate limit." in joined
    assert ELISION in joined
    for source, text in zip([AUTH, OTHER], out.texts, strict=True):
        if text is None:
            continue
        for run in _runs(text.strip(ELISION).strip("\n")):
            assert run.strip(ELISION).strip() in source  # never altered


def test_code_blocks_and_tables_are_whole_or_absent() -> None:
    query = "How do I pass api_key to Client?"
    out = compact_pieces(query, [AUTH], [0.9], 400)
    text = out.texts[0]
    assert text is not None
    assert "```python\nclient = Client(api_key=KEY)\nclient.get('/v1')\n```" in text
    assert text.count("```") == 2
    if "| header |" in text:
        assert "| header | meaning |\n|---|---|\n| X-Api-Key | your key |" in text


def test_heading_comes_with_its_kept_units() -> None:
    out = compact_pieces("rate limit header", [OTHER], [0.5], 200)
    text = out.texts[0]
    assert text is not None and text.startswith("## Rate limits")


def test_no_overlap_falls_back_to_whole_chunks() -> None:
    small = "Short chunk about nothing."
    out = compact_pieces("zebra quantum", [AUTH, small], [0.1, 0.9], 50)
    assert not out.pruned and not out.dropped_relevant
    assert out.texts == [None, small]


def test_deterministic() -> None:
    args = ("api key header", [AUTH, OTHER], [0.9, 0.5], 250)
    assert compact_pieces(*args) == compact_pieces(*args)


def test_tight_budget_flags_dropped_relevant_units() -> None:
    out = compact_pieces("X-Api-Key header key", [AUTH, OTHER], [0.9, 0.5], 30)
    assert out.dropped_relevant
    assert sum(count_tokens(t) for t in out.texts if t) <= 30


def _fragment(chunk: str, md: str, rerank: float) -> Fragment:
    return Fragment(
        chunk_id=chunk,
        document_id="doc_a",
        version_id="ver_a",
        source_url="https://docs.example.com/auth",
        heading_path=["Auth"],
        md=md,
        tokens=count_tokens(md),
        scores=FragmentScores(lexical_rank=1, vector_rank=1, rrf=0.03, rerank=rerank),
        evidence=[],
    )


def test_compact_result_rewrites_md_tokens_and_budget() -> None:
    result = AssembleResult(
        query="q",
        fragments=[_fragment("1", AUTH, 0.9), _fragment("2", OTHER, 0.4)],
        budget_tokens=pool_budget(300),
    )
    result.output_tokens = sum(f.tokens for f in result.fragments)
    compact_result(result, query="Which header carries the API key?", budget=300)
    assert result.compact == "pruned"
    assert result.budget_tokens == 300
    assert result.output_tokens == sum(f.tokens for f in result.fragments) <= 300
    for frag in result.fragments:
        assert frag.tokens == count_tokens(frag.md)
        assert frag.heading_path == ["Auth"] and frag.chunk_id in {"1", "2"}


PT_QUESTION = "Como faço a instalação do pacote?"


def test_no_overlap_is_reported_as_unmatched() -> None:
    out = compact_pieces(PT_QUESTION, [AUTH], [0.9], 2000)
    assert not out.matched and not out.pruned
    assert compact_pieces(
        "Which header carries the API key?", [AUTH], [0.9], 2000
    ).matched


def test_portuguese_question_over_english_page_sets_no_match() -> None:
    result = AssembleResult(
        query="q",
        fragments=[_fragment("1", AUTH, 0.9)],
        budget_tokens=pool_budget(2000),
    )
    compact_result(result, query=PT_QUESTION, budget=2000)
    assert result.compact == "no_match"
    assert result.fragments[0].md == AUTH


def test_accented_query_matches_unaccented_page_text_verbatim() -> None:
    page = "## Setup\n\nRun the instalacao step first. Unrelated gardening text here."
    out = compact_pieces("Qual a instalação?", [page], [0.9], 2000)
    assert out.matched
    assert out.texts[0] is not None and "Run the instalacao step first." in out.texts[0]


def test_portuguese_function_words_are_not_terms() -> None:
    out = compact_pieces("Como para uma de do da em", [AUTH], [0.9], 2000)
    assert not out.matched
