"""Grade a benchmark answer: correctness + citation precision (SPEC §10.4).

Correctness depends on the task's ``answer_type``:

* ``exact`` — the answer must equal one expected string (normalized: lowercased,
  whitespace collapsed);
* ``contains_all`` — the normalized answer must contain every expected string;
* ``judge`` — a DIFFERENT model (the judge, SPEC §10.4) rules on correctness via
  a fixed rubric, returning ``{"correct": bool, "reason": str}``.

Unanswerable tasks (``expected == ["INSUFFICIENT"]``) are correct exactly when
the answerer returned the INSUFFICIENT sentinel.

Citation precision (SPEC §10.4): a citation is counted correct when its quote
matches some text in the cited document of the frozen store with
``difflib.SequenceMatcher.ratio() >= 0.9`` AND overlaps one of the task's
``expected_evidence`` quotes. ``citation_correct`` is ``True`` when at least one
citation meets both tests. The judge provider is injected; tests use the fake.
"""

from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass

from app.fetch.detect import parse_content_type
from app.providers.llm.base import LLMProvider
from bench.answer import INSUFFICIENT, AnswerResult
from bench.corpus_store import CorpusStore
from bench.tasks_schema import Task

#: Minimum SequenceMatcher ratio for a quote to count as present (SPEC §10.4).
_CITATION_RATIO = 0.9

_WS_RE = re.compile(r"\s+")
_TAG_RE = re.compile(r"<[^>]+>")

#: Fixed judge rubric (SPEC §10.4). Contains the words "judge"/"rubric" and
#: "correct" so the deterministic fake emits the judge JSON shape (see
#: FEAT-001 findings `fake_json_shapes`).
_JUDGE_TEMPLATE = """You are the judge. Apply this rubric and decide \
if the answer is correct.

An answer is correct when it is faithful to the expected answer(s) and supported
by the source documentation. Judge meaning, not wording.

Return a single JSON object: {{"correct": <true|false>, "reason": "<short reason>"}}

Question:
{question}

Expected answer(s):
{expected}

Model answer:
{answer}
"""


@dataclass(frozen=True)
class GradeResult:
    """The outcome of grading one answer against its task.

    ``correct`` is overall correctness; ``citation_correct`` whether at least one
    citation is verified against the corpus and expected evidence;
    ``is_insufficient`` whether the answer was the INSUFFICIENT sentinel;
    ``reason`` is a short human explanation (the judge's for ``judge`` tasks).
    """

    correct: bool
    citation_correct: bool
    is_insufficient: bool
    reason: str


def _normalize(text: str) -> str:
    """Lowercase ``text`` and collapse runs of whitespace to single spaces."""
    return _WS_RE.sub(" ", text).strip().lower()


def _flatten_html(raw: bytes, content_type: str) -> str:
    """Decode and strip ``raw`` to whitespace-collapsed plain text."""
    _media, charset = parse_content_type(content_type)
    decoded = raw.decode(charset or "utf-8", errors="replace")
    stripped = _TAG_RE.sub(" ", decoded)
    return _WS_RE.sub(" ", stripped).strip()


def _best_ratio(needle: str, haystack: str) -> float:
    """Return the best SequenceMatcher ratio of ``needle`` within ``haystack``.

    A literal substring scores 1.0 immediately. Otherwise the best overlapping
    window of ``haystack`` (same length as ``needle``) is compared, so a quote
    is matched against the region it aligns to rather than the whole document.
    """
    if not needle:
        return 0.0
    needle_n = _normalize(needle)
    hay_n = _normalize(haystack)
    if needle_n in hay_n:
        return 1.0
    matcher = difflib.SequenceMatcher(None, needle_n, hay_n)
    block = matcher.find_longest_match(0, len(needle_n), 0, len(hay_n))
    if block.size == 0:
        return 0.0
    start = max(0, block.b - block.a)
    window = hay_n[start : start + len(needle_n)]
    return difflib.SequenceMatcher(None, needle_n, window).ratio()


def _grade_correct(
    task: Task, result: AnswerResult, *, judge: LLMProvider
) -> tuple[bool, str]:
    """Decide correctness per ``answer_type`` (and INSUFFICIENT), with a reason."""
    # Unanswerable task: correct iff the model declined (SPEC §10.4).
    if task.expected == [INSUFFICIENT]:
        if result.is_insufficient:
            return True, "answer is INSUFFICIENT as expected"
        return False, "expected INSUFFICIENT but the model answered"

    # The model declined on an answerable task: never correct.
    if result.is_insufficient:
        return False, "model answered INSUFFICIENT but the task is answerable"

    normalized = _normalize(result.answer)

    if task.answer_type == "exact":
        for expected in task.expected:
            if normalized == _normalize(expected):
                return True, "exact match"
        return False, "no exact match among expected answers"

    if task.answer_type == "contains_all":
        missing = [e for e in task.expected if _normalize(e) not in normalized]
        if not missing:
            return True, "answer contains all expected substrings"
        return False, f"missing expected substrings: {missing}"

    # judge: a different model rules via the fixed rubric (SPEC §10.4).
    prompt = _JUDGE_TEMPLATE.format(
        question=task.question,
        expected="\n".join(task.expected),
        answer=result.answer,
    )
    completion = judge.complete(prompt, response_format="json")
    try:
        payload = json.loads(completion.text)
    except (ValueError, TypeError):
        payload = None
    if isinstance(payload, dict) and isinstance(payload.get("correct"), bool):
        reason = payload.get("reason")
        return payload["correct"], reason if isinstance(
            reason, str
        ) else "judge verdict"
    return False, "judge returned an unparseable verdict"


def _grade_citations(task: Task, result: AnswerResult, *, store: CorpusStore) -> bool:
    """Return whether at least one citation is verified (SPEC §10.4).

    A citation counts when its quote is present in the cited document
    (``ratio >= 0.9``) AND it overlaps one of the task's expected-evidence
    quotes (``ratio >= 0.9``), so an invented quote — even if it happens to
    appear in the doc — fails unless it matches the evidence the task declared.
    """
    if not task.expected_evidence:
        return False

    for citation in result.citations:
        try:
            raw = store.raw_bytes(citation.url)
        except (KeyError, ValueError):
            continue
        doc_text = _flatten_html(raw, store.content_type(citation.url))
        if _best_ratio(citation.quote, doc_text) < _CITATION_RATIO:
            continue
        # Must overlap an expected-evidence quote for the SAME url.
        for evidence in task.expected_evidence:
            if evidence.url != citation.url:
                continue
            if _best_ratio(citation.quote, evidence.quote) >= _CITATION_RATIO:
                return True
    return False


def grade(
    task: Task,
    answer_result: AnswerResult,
    *,
    store: CorpusStore,
    judge_provider: LLMProvider,
) -> GradeResult:
    """Grade ``answer_result`` for ``task`` (SPEC §10.4).

    Correctness follows the task's ``answer_type`` (deterministic ``exact``/
    ``contains_all`` or the injected ``judge_provider`` for ``judge``), with the
    INSUFFICIENT convention handled first. Citation precision is checked against
    the frozen ``store`` and the task's expected evidence.
    """
    correct, reason = _grade_correct(task, answer_result, judge=judge_provider)
    citation_correct = _grade_citations(task, answer_result, store=store)
    return GradeResult(
        correct=correct,
        citation_correct=citation_correct,
        is_insufficient=answer_result.is_insufficient,
        reason=reason,
    )
