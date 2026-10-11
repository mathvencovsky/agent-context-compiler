"""Answerer with the FAKE provider: JSON shape, INSUFFICIENT, determinism.

The fake emits the answerer JSON shape ``{"answer", "citations"}`` for
``response_format="json"``, so ``bench.answer`` parses a well-formed result
without any network (SPEC §10.3). These tests cover the happy shape, the
INSUFFICIENT fallback for malformed output, and determinism.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.providers.llm.base import LLMResult, ResponseFormat
from app.providers.llm.fake import FakeLLMProvider
from bench.answer import INSUFFICIENT, AnswerResult, answer, build_prompt
from bench.tasks_schema import Evidence, Task


def _task() -> Task:
    url = "https://docs.python.org/3/library/functions.html#abs"
    return Task(
        id="t1",
        category="api_reference",
        split="dev",
        urls=[url],
        question="What does abs return?",
        answer_type="judge",
        expected=["absolute value"],
        expected_evidence=[Evidence(url=url, quote="absolute value")],
        author="human",
        validated=True,
    )


def test_answer_returns_shape_and_tokens() -> None:
    result = answer(
        _task(), "abs returns the absolute value.", provider=FakeLLMProvider()
    )
    assert isinstance(result, AnswerResult)
    assert isinstance(result.answer, str) and result.answer
    assert result.input_tokens > 0
    assert result.output_tokens > 0


def test_answer_is_deterministic() -> None:
    provider = FakeLLMProvider()
    first = answer(_task(), "context text", provider=provider)
    second = answer(_task(), "context text", provider=provider)
    assert first == second


def test_prompt_is_fixed_and_contains_question_and_context() -> None:
    prompt = build_prompt(_task(), "my-context")
    assert "What does abs return?" in prompt
    assert "my-context" in prompt
    assert "INSUFFICIENT" in prompt  # fixed instructions mention the sentinel


@dataclass
class _InsufficientProvider:
    """A provider whose answer is the INSUFFICIENT sentinel."""

    model: str = "stub"

    def complete(
        self,
        prompt: str,
        *,
        seed: int = 7,
        response_format: ResponseFormat = "text",
    ) -> LLMResult:
        return LLMResult(
            text='{"answer": "INSUFFICIENT", "citations": []}',
            input_tokens=3,
            output_tokens=2,
            model=self.model,
        )


def test_answer_detects_insufficient() -> None:
    result = answer(_task(), "", provider=_InsufficientProvider())
    assert result.answer == INSUFFICIENT
    assert result.is_insufficient is True


@dataclass
class _BrokenProvider:
    """A provider that returns non-JSON text."""

    model: str = "stub"

    def complete(
        self,
        prompt: str,
        *,
        seed: int = 7,
        response_format: ResponseFormat = "text",
    ) -> LLMResult:
        return LLMResult(
            text="not json at all", input_tokens=1, output_tokens=1, model=self.model
        )


def test_unparseable_output_falls_back_to_insufficient() -> None:
    result = answer(_task(), "ctx", provider=_BrokenProvider())
    assert result.is_insufficient is True
    assert result.citations == []
