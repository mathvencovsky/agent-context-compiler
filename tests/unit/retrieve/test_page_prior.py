"""M10b page prior: page text, BM25 over pages, the two ways it is used."""

from __future__ import annotations

from app.retrieve.lexical import LexicalConfig, LexicalItem, rank_lexical
from app.retrieve.page_prior import (
    apply_page_rrf,
    page_norm,
    page_query,
    page_ranks,
    page_scores,
    page_texts,
    url_slug_text,
)

_URLS = {
    "react": "https://react.dev/learn/preserving-and-resetting-state",
    "python": "https://docs.python.org/3/library/os.path.html",
}
_ITEMS = [
    LexicalItem(("python", 0), "os.path\n\njoin paths", ["os.path"], "join paths"),
    LexicalItem(("python", 1), "x", ["os.path", "Functions"], "join"),
    LexicalItem(("react", 0), "x", ["Preserving state"], "state words"),
]


def test_url_slug_text_keeps_path_words() -> None:
    assert url_slug_text(_URLS["python"]) == "3 library os path"
    assert url_slug_text(_URLS["react"]) == "learn preserving and resetting state"
    assert url_slug_text("") == ""


def test_page_query_takes_the_first_words() -> None:
    assert page_query("a b  c\nd e", 3) == "a b c"


def test_page_texts_hold_slug_and_distinct_headings() -> None:
    texts = page_texts(_ITEMS, _URLS)
    assert texts["python"].splitlines() == ["3 library os path", "os.path", "Functions"]
    assert "resetting" in texts["react"]


def test_page_scores_prefer_the_matching_page() -> None:
    cfg = LexicalConfig(page_prior="rrf")
    scores = page_scores(_ITEMS, "How to reset state in React?", cfg, _URLS)
    assert scores["react"] > scores["python"]
    assert page_ranks(scores) == {"react": 1}


def test_page_query_words_cut_the_query() -> None:
    cfg = LexicalConfig(page_prior="rrf", page_prior_query_words=2)
    scores = page_scores(_ITEMS, "os path ... reset state", cfg, _URLS)
    assert page_ranks(scores) == {"python": 1}


def test_page_ranks_ignore_zero_and_break_ties_on_id() -> None:
    assert page_ranks({"b": 1.0, "a": 1.0, "c": 0.0}) == {"a": 1, "b": 2}


def test_page_norm() -> None:
    assert page_norm({"a": 2.0, "b": 1.0, "c": 0.0}) == {"a": 1.0, "b": 0.5, "c": 0.0}
    assert page_norm({"a": 1.0, "b": 1.0}) == {"a": 0.0, "b": 0.0}
    assert page_norm({}) == {}


def test_apply_page_rrf_noop_without_ranks() -> None:
    fused = [(("a", 0), 0.5), (("b", 0), 0.4)]
    assert apply_page_rrf(fused, {}, weight=0.5, k=60) is fused


def test_apply_page_rrf_lifts_the_ranked_page() -> None:
    fused = [(("a", 0), 1 / 61), (("b", 0), 1 / 62)]
    out = apply_page_rrf(fused, {"b": 1}, weight=0.5, k=60)
    assert [k for k, _ in out] == [("b", 0), ("a", 0)]
    assert out[0][1] == 1 / 62 + 0.5 / 61


def test_rank_lexical_off_has_no_page_rank() -> None:
    result = rank_lexical(_ITEMS, "join", url_by_document=_URLS)
    assert result.page_rank == {}


def test_rank_lexical_rrf_returns_page_ranks() -> None:
    cfg = LexicalConfig(page_prior="rrf")
    result = rank_lexical(_ITEMS, "reset state join", cfg, url_by_document=_URLS)
    assert result.page_rank["react"] == 1


def test_rank_lexical_multiply_boosts_the_page() -> None:
    items = [
        LexicalItem(("python", 0), "join join", [], "join join"),
        LexicalItem(("react", 0), "join", ["Resetting state"], "join"),
    ]
    off = rank_lexical(items, "reset state join", url_by_document=_URLS)
    mult = rank_lexical(
        items,
        "reset state join",
        LexicalConfig(page_prior="multiply", page_prior_alpha=1.5),
        url_by_document=_URLS,
    )
    assert off.ranks[0] == ("python", 0)
    assert mult.ranks[0] == ("react", 0)
    assert mult.page_rank == {}
