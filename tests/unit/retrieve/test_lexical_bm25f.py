"""M10b lexical scorers: tokenizer, symbols, fields, BM25F (offline, pure).

The default ``LexicalConfig`` must reproduce ``overlap_ranks`` exactly; every
other knob is opt-in (DECISIONS 2026-10-29).
"""

from __future__ import annotations

import pytest

from app.retrieve.lexical import (
    LexicalConfig,
    LexicalItem,
    bm25f_scores,
    chunk_fields,
    identifier_tokenize,
    lexical_ranks,
    overlap_ranks,
    query_symbols,
    tokenize,
)


def _item(doc: str, ord_: int, headings: list[str], md: str) -> LexicalItem:
    prefix = " > ".join(headings) + "\n\n" if headings else ""
    return LexicalItem(key=(doc, ord_), text=prefix + md, heading_path=headings, md=md)


# --------------------------------------------------------------------------- #
# Tokenizer
# --------------------------------------------------------------------------- #
def test_identifier_tokens_keep_whole_and_parts() -> None:
    tokens = identifier_tokenize("Call `os.path.join()` with useState and snake_case.")
    assert "os.path.join" in tokens
    assert {"os", "path", "join"} <= set(tokens)
    assert {"usestate", "use", "state"} <= set(tokens)
    assert {"snake_case", "snake", "case"} <= set(tokens)


def test_identifier_tokens_split_acronyms_before_lowercasing() -> None:
    tokens = identifier_tokenize("HTTPServer")
    assert tokens == ["httpserver", "http", "server"]


def test_identifier_tokens_equal_plain_tokens_on_prose() -> None:
    text = "The quick brown fox doesn't jump over 3 lazy dogs, ok?"
    assert identifier_tokenize(text) == tokenize(text)


def test_identifier_tokens_do_not_duplicate_a_plain_word() -> None:
    assert identifier_tokenize("reset") == ["reset"]


def test_query_symbols_finds_code_names_only() -> None:
    query = "Use os.path.join() or useState in my_func? import this e.g. 3.12 built-in"
    assert query_symbols(query) == ["os.path.join", "usestate", "my_func"]


def test_query_symbols_skip_keywords_and_short_words() -> None:
    assert query_symbols("a.b x_y return_value") == ["return_value"]
    assert query_symbols("import") == []


# --------------------------------------------------------------------------- #
# Fields
# --------------------------------------------------------------------------- #
def test_chunk_fields_split_headings_prose_and_code() -> None:
    md = "Intro text.\n\n```python\nos.path.join(a, b)\n```\n\nAfter."
    fields = chunk_fields(["Page", "Section", "Leaf"], md)
    assert fields.leaf == "Leaf"
    assert fields.ancestors == "Page Section"
    assert "os.path.join" in fields.code
    assert "os.path" not in fields.prose
    assert "Intro text." in fields.prose and "After." in fields.prose


def test_chunk_fields_without_headings() -> None:
    fields = chunk_fields([], "just text")
    assert (fields.leaf, fields.ancestors, fields.prose, fields.code) == (
        "",
        "",
        "just text",
        "",
    )


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
def test_default_config_is_today() -> None:
    assert LexicalConfig().is_default
    assert not LexicalConfig(scorer="bm25f").is_default


@pytest.mark.parametrize(
    "kwargs",
    [
        {"symbol_bonus": 5.0},
        {"scorer": "bm25f", "symbol_bonus": 5.0},
        {"coverage": True},
        {"scorer": "bm25f", "title_weight": 0.0},
        {"page_prior_query_words": 0},
    ],
)
def test_invalid_configs_are_refused(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        LexicalConfig(**kwargs)  # type: ignore[arg-type]


def test_from_settings_reads_every_knob() -> None:
    from app.config import Settings

    settings = Settings(
        lexical_scorer="bm25f",
        lexical_title_weight=5.0,
        lexical_identifiers=True,
        lexical_symbol_bonus=5.0,
        lexical_coverage=True,
        page_prior="rrf",
        page_prior_weight=0.25,
        page_prior_alpha=1.0,
        page_prior_query_words=16,
    )
    assert LexicalConfig.from_settings(settings) == LexicalConfig(
        scorer="bm25f",
        title_weight=5.0,
        identifiers=True,
        symbol_bonus=5.0,
        coverage=True,
        page_prior="rrf",
        page_prior_weight=0.25,
        page_prior_alpha=1.0,
        page_prior_query_words=16,
    )


def test_settings_defaults_give_the_default_config() -> None:
    from app.config import Settings

    assert LexicalConfig.from_settings(Settings()) == LexicalConfig()


# --------------------------------------------------------------------------- #
# Ranking
# --------------------------------------------------------------------------- #
_ITEMS = [
    _item("d1", 0, ["Intro"], "state state state component words words words"),
    _item("d1", 1, ["Guide", "Resetting state"], "How to reset at the same position."),
    _item("d2", 0, ["API", "os.path"], "```python\nos.path.join(a, b)\n```\nJoin."),
    _item("d2", 1, ["API", "Other"], "Unrelated text about files."),
]


def test_default_lexical_ranks_equal_overlap_ranks() -> None:
    query = "How do I reset state in a component? os.path.join"
    expected = overlap_ranks(((i.key, i.text) for i in _ITEMS), query, 50)
    assert lexical_ranks(_ITEMS, query) == expected
    assert lexical_ranks(_ITEMS, query, LexicalConfig()) == expected


def test_heading_weight_lifts_the_chunk_whose_heading_matches() -> None:
    query = "resetting state"
    low = lexical_ranks(_ITEMS, query, LexicalConfig(scorer="bm25f", title_weight=1))
    high = lexical_ranks(_ITEMS, query, LexicalConfig(scorer="bm25f", title_weight=5))
    assert high[0] == ("d1", 1)
    assert high.index(("d1", 1)) <= low.index(("d1", 1))


def test_bm25f_saturates_repeated_common_words() -> None:
    # Overlap counts every occurrence: the chunk repeating "state" wins.
    query = "reset state"
    assert lexical_ranks(_ITEMS, query)[0] == ("d1", 0)
    # BM25F saturates the repeats and weights the leaf heading: the section
    # about resetting state wins.
    bm25f = lexical_ranks(_ITEMS, query, LexicalConfig(scorer="bm25f"))
    assert bm25f[0] == ("d1", 1)


def test_bm25f_drops_chunks_without_query_terms() -> None:
    ranks = lexical_ranks(_ITEMS, "join", LexicalConfig(scorer="bm25f"))
    assert ranks == [("d2", 0)]


def test_bm25f_empty_inputs() -> None:
    assert bm25f_scores([], "x", LexicalConfig(scorer="bm25f")) == []
    assert bm25f_scores(_ITEMS, "  ", LexicalConfig(scorer="bm25f")) == []


def test_identifier_tokens_match_camel_case_against_spaced_words() -> None:
    items = [
        _item("d", 0, ["Hooks"], "Call use state to add state."),
        _item("d", 1, ["Other"], "Something else entirely."),
    ]
    plain = lexical_ranks(items, "useState", LexicalConfig(scorer="bm25f"))
    ident = lexical_ranks(
        items, "useState", LexicalConfig(scorer="bm25f", identifiers=True)
    )
    assert plain == []
    assert ident == [("d", 0)]


def test_identifiers_work_with_the_overlap_scorer() -> None:
    items = [_item("d", 0, [], "use state here")]
    assert lexical_ranks(items, "useState") == []
    assert lexical_ranks(items, "useState", LexicalConfig(identifiers=True)) == [
        ("d", 0)
    ]


def test_symbol_bonus_prefers_the_whole_symbol() -> None:
    items = [
        _item("d", 0, ["Paths"], "os path join are words here, path join path."),
        _item("d", 1, ["Paths"], "Use os.path.join to build it."),
    ]
    query = "what does os.path.join do with path join"
    base = LexicalConfig(scorer="bm25f", identifiers=True)
    with_bonus = LexicalConfig(scorer="bm25f", identifiers=True, symbol_bonus=5.0)
    scores = dict((k, s) for s, k in bm25f_scores(items, query, base))
    bonus = dict((k, s) for s, k in bm25f_scores(items, query, with_bonus))
    assert bonus[("d", 1)] > scores[("d", 1)]
    assert lexical_ranks(items, query, with_bonus)[0] == ("d", 1)


def test_coverage_factor_rewards_distinct_terms() -> None:
    items = [
        _item("d", 0, [], "alpha alpha alpha alpha alpha alpha"),
        _item("d", 1, [], "alpha beta gamma"),
        _item("d", 2, [], "nothing here"),
    ]
    query = "alpha beta gamma"
    plain = dict((k, s) for s, k in bm25f_scores(items, query, LexicalConfig("bm25f")))
    cov = dict(
        (k, s)
        for s, k in bm25f_scores(items, query, LexicalConfig("bm25f", coverage=True))
    )
    # The chunk holding every distinct term gains more than the repeater.
    assert cov[("d", 1)] / plain[("d", 1)] > cov[("d", 0)] / plain[("d", 0)]


def test_bm25f_is_deterministic_and_breaks_ties_on_the_key() -> None:
    items = [_item("b", 0, [], "same text"), _item("a", 0, [], "same text")]
    cfg = LexicalConfig(scorer="bm25f")
    assert lexical_ranks(items, "same", cfg) == [("a", 0), ("b", 0)]
    assert lexical_ranks(items, "same", cfg) == lexical_ranks(
        list(reversed(items)), "same", cfg
    )


def test_token_cache_does_not_change_results() -> None:
    cfg = LexicalConfig(scorer="bm25f", identifiers=True)
    cache: dict = {}  # type: ignore[type-arg]
    first = lexical_ranks(_ITEMS, "reset os.path.join", cfg, token_cache=cache)
    assert cache
    again = lexical_ranks(_ITEMS, "reset os.path.join", cfg, token_cache=cache)
    assert first == again == lexical_ranks(_ITEMS, "reset os.path.join", cfg)


def test_limit_caps_the_ranking() -> None:
    items = [_item("d", i, [], f"word {i}") for i in range(10)]
    assert len(lexical_ranks(items, "word", LexicalConfig("bm25f"), limit=3)) == 3
