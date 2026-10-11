"""FakeLLMProvider: determinism, distinct prompts, valid JSON, token accounting."""

from __future__ import annotations

import json

from app.index.tokens import count_tokens
from app.providers.llm.base import LLMProvider, LLMResult
from app.providers.llm.fake import FakeLLMProvider


def test_is_llmprovider() -> None:
    assert isinstance(FakeLLMProvider(), LLMProvider)


def test_same_prompt_is_byte_identical() -> None:
    provider = FakeLLMProvider()
    first = provider.complete("What is the default port?")
    second = provider.complete("What is the default port?")
    assert first == second
    assert isinstance(first, LLMResult)


def test_different_prompts_differ() -> None:
    provider = FakeLLMProvider()
    a = provider.complete("question A")
    b = provider.complete("question B")
    assert a.text != b.text


def test_response_format_changes_output() -> None:
    provider = FakeLLMProvider()
    text = provider.complete("same prompt", response_format="text")
    as_json = provider.complete("same prompt", response_format="json")
    assert text.text != as_json.text


def test_json_is_parseable_answer_shape() -> None:
    provider = FakeLLMProvider()
    result = provider.complete("What does the function return?", response_format="json")
    payload = json.loads(result.text)
    assert set(payload) == {"answer", "citations"}
    assert isinstance(payload["answer"], str)
    assert payload["citations"] == []


def test_json_is_parseable_judge_shape() -> None:
    provider = FakeLLMProvider()
    judge_prompt = "You are the judge. Apply the rubric and decide if correct."
    result = provider.complete(judge_prompt, response_format="json")
    payload = json.loads(result.text)
    assert set(payload) == {"correct", "reason"}
    assert isinstance(payload["correct"], bool)
    assert isinstance(payload["reason"], str)


def test_json_determinism() -> None:
    provider = FakeLLMProvider()
    first = provider.complete("q", response_format="json")
    second = provider.complete("q", response_format="json")
    assert first.text == second.text


def test_input_tokens_match_count_tokens() -> None:
    provider = FakeLLMProvider()
    prompt = "a prompt whose tokens we count"
    result = provider.complete(prompt)
    assert result.input_tokens == count_tokens(prompt)
    assert result.output_tokens == count_tokens(result.text)


def test_model_name_surfaced() -> None:
    provider = FakeLLMProvider(model="fake-xyz")
    assert provider.model == "fake-xyz"
    assert provider.complete("hi").model == "fake-xyz"
