"""Grader coverage (acceptance criterion (c)): the five grading cases.

Covers exact, contains_all, a valid citation, an invented citation, and the
INSUFFICIENT path INCLUDING INSUFFICIENT counted as a correct answer. The judge
provider is a deterministic stub (never the network); correctness for
exact/contains_all is purely deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from app.providers.llm.base import LLMResult, ResponseFormat
from bench.answer import AnswerResult, Citation
from bench.corpus_store import CorpusStore
from bench.grade import grade
from bench.tasks_schema import Evidence, Task

_ABS_URL = "https://docs.python.org/3/library/functions.html#abs"
_FIXTURE_CORPUS = Path(__file__).resolve().parents[2] / "fixtures" / "bench" / "corpus"


@pytest.fixture
def store() -> CorpusStore:
    return CorpusStore(_FIXTURE_CORPUS)


@dataclass
class _Judge:
    """A judge stub returning a fixed verdict."""

    verdict: bool
    model: str = "judge-stub"

    def complete(
        self,
        prompt: str,
        *,
        temperature: float = 0.0,
        response_format: ResponseFormat = "text",
    ) -> LLMResult:
        import json

        text = json.dumps({"correct": self.verdict, "reason": "stub"})
        return LLMResult(text=text, input_tokens=5, output_tokens=3, model=self.model)


def _task(answer_type: str, expected: list[str], evidence: list[Evidence]) -> Task:
    return Task(
        id="t",
        category="api_reference",
        split="dev",
        urls=[_ABS_URL],
        question="What does abs return?",
        answer_type=answer_type,  # type: ignore[arg-type]
        expected=expected,
        expected_evidence=evidence,
        author="human",
        validated=True,
    )


def _answer(text: str, citations: list[Citation] | None = None) -> AnswerResult:
    return AnswerResult(
        answer=text, citations=citations or [], input_tokens=1, output_tokens=1
    )


def test_exact_match(store: CorpusStore) -> None:
    task = _task("exact", ["the absolute value of a number"], [])
    result = grade(
        task,
        _answer("The absolute value of a number"),  # case-insensitive
        store=store,
        judge_provider=_Judge(verdict=False),
    )
    assert result.correct is True


def test_exact_mismatch(store: CorpusStore) -> None:
    task = _task("exact", ["the absolute value of a number"], [])
    result = grade(
        task, _answer("something else"), store=store, judge_provider=_Judge(False)
    )
    assert result.correct is False


def test_contains_all(store: CorpusStore) -> None:
    task = _task("contains_all", ["FLASK_APP", "flask run"], [])
    good = grade(
        task,
        _answer("Set FLASK_APP then use flask run to start it."),
        store=store,
        judge_provider=_Judge(False),
    )
    assert good.correct is True
    bad = grade(
        task,
        _answer("Set FLASK_APP only."),
        store=store,
        judge_provider=_Judge(False),
    )
    assert bad.correct is False


def test_valid_citation(store: CorpusStore) -> None:
    task = _task(
        "judge",
        ["absolute value"],
        [Evidence(url=_ABS_URL, quote="Return the absolute value of a number.")],
    )
    result = grade(
        task,
        _answer(
            "It returns the absolute value.",
            [Citation(url=_ABS_URL, quote="Return the absolute value of a number.")],
        ),
        store=store,
        judge_provider=_Judge(verdict=True),
    )
    assert result.correct is True
    assert result.citation_correct is True


def test_invented_citation(store: CorpusStore) -> None:
    task = _task(
        "judge",
        ["absolute value"],
        [Evidence(url=_ABS_URL, quote="Return the absolute value of a number.")],
    )
    result = grade(
        task,
        _answer(
            "It returns the absolute value.",
            [
                Citation(
                    url=_ABS_URL,
                    quote="This sentence is nowhere in the document at all.",
                )
            ],
        ),
        store=store,
        judge_provider=_Judge(verdict=True),
    )
    assert result.citation_correct is False


def test_insufficient_is_correct_when_expected(store: CorpusStore) -> None:
    task = _task("judge", ["INSUFFICIENT"], [])
    result = grade(
        task, _answer("INSUFFICIENT"), store=store, judge_provider=_Judge(False)
    )
    assert result.correct is True
    assert result.is_insufficient is True


def test_insufficient_is_wrong_when_answerable(store: CorpusStore) -> None:
    task = _task("exact", ["the absolute value of a number"], [])
    result = grade(
        task, _answer("INSUFFICIENT"), store=store, judge_provider=_Judge(True)
    )
    assert result.correct is False
    assert result.is_insufficient is True


def test_judge_verdict_used(store: CorpusStore) -> None:
    task = _task("judge", ["anything"], [])
    answer_result = _answer("some free-form answer")
    assert (
        grade(task, answer_result, store=store, judge_provider=_Judge(True)).correct
        is True
    )
    assert (
        grade(task, answer_result, store=store, judge_provider=_Judge(False)).correct
        is False
    )
