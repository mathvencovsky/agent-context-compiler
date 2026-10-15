"""Unit tests for app.index.tokens.count_tokens (M5)."""

from __future__ import annotations

from app.index.tokens import count_tokens


def test_empty_string_is_zero() -> None:
    assert count_tokens("") == 0


def test_non_empty_text_is_positive() -> None:
    assert count_tokens("hello world") > 0


def test_deterministic_across_calls() -> None:
    first = count_tokens("the quick brown fox")
    second = count_tokens("the quick brown fox")
    assert first == second


def test_known_stable_value() -> None:
    # "hello world" is a stable, well-known cl100k_base tokenization (2 tokens).
    assert count_tokens("hello world") == 2


def test_special_token_text_counts_as_plain_text() -> None:
    # Docs about LLMs quote "<|endoftext|>"; tiktoken's default encode() raises.
    # As plain text it is several ordinary tokens, never the 1 special token.
    assert (
        count_tokens("the model printed <|endoftext|> early")
        > count_tokens("the model printed early") + 1
    )
