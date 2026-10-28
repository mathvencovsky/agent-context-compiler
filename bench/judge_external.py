"""Grade M10 agent answers against the accepted answer (PREREG §9, Appendix B).

Both judges (primary ``gpt-6.1-sol``, second ``claude-opus-5-5``) and the human
graders apply :data:`RUBRIC`, the PREREG Amendment 13 text verbatim (it replaces
the Appendix B rubric); a test checks it against ``bench/PREREG.md``. The judge
reasons before its verdict, a list of alternatives with a wrong one is
incorrect, and ``abstained`` records an answer that only says it cannot answer. The candidate is the agent's final answer, taken
from its last message by the PREREG §4 rule (:func:`final_answer`).

A judge reply that is not a JSON object with a boolean ``correct`` is asked for
again, up to :data:`RETRIES` more times with a new seed each time. If it still
fails, the verdict is ``None`` and the session counts as an infrastructure
failure, excluded from every arm (PREREG Amendment 5). Unlike ``bench/grade.py``
it is never scored as incorrect.

Citation precision for these tasks is ``bench.grade._grade_citations``: it only
reads ``expected_evidence`` and ``citations``, so it already works with an
:class:`~bench.tasks_schema.ExternalTask` and the :class:`AnswerResult` here.

Providers are injected; tests use scripted fakes, so nothing here touches the
network.
"""

# ruff: noqa: E501 - RUBRIC must stay byte-identical to the PREREG block.
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass

from app.providers.llm.base import DEFAULT_SEED, LLMProvider
from bench.answer import AnswerResult, _parse_citations
from bench.external_tasks import SEED
from bench.tasks_schema import ExternalTask

#: PREREG Amendment 13 judge rubric, verbatim (used by both judges and the humans).
RUBRIC = """You are the judge. Decide whether the CANDIDATE answer resolves the developer's QUESTION.
The REFERENCE is the answer the asker accepted on Stack Overflow; treat what it says as correct.
Mark the candidate correct if its core answer (the fact, setting, API or fix that resolves the question)
agrees with the reference, even if shorter or worded differently, or if it is a different solution that
clearly resolves the question as asked.
Mark it incorrect if its core answer is wrong, contradicts the reference, is missing, or only says the
information is unavailable. Also mark it incorrect if it offers several alternative core answers and any
of them is wrong or contradicts the reference. Ignore style, length and citations.
Set "abstained" to true only if the candidate gives no core answer and says that the information is
unavailable or that it does not know.
Write your reasoning first, then the verdict, as a single JSON object:
{"reason": "<short reasoning>", "abstained": true|false, "correct": true|false}

QUESTION:
<question title and body>

REFERENCE:
<accepted answer>

CANDIDATE:
<candidate answer>"""

#: The rubric's lines for judges only (abstention flag and JSON); humans skip them.
JUDGE_ONLY_LINES = (
    'Set "abstained" to true only if the candidate gives no core answer and says that the information is\n'
    "unavailable or that it does not know.\n"
    "Write your reasoning first, then the verdict, as a single JSON object:\n"
    '{"reason": "<short reasoning>", "abstained": true|false, "correct": true|false}\n'
)

#: Extra judge attempts after an unparseable reply (PREREG Amendment 5).
RETRIES = 2
#: PREREG §9: share of main-run sessions judged twice by the primary judge.
RECHECK_FRACTION = 0.10

_SLOTS = ("<question title and body>", "<accepted answer>", "<candidate answer>")
_SLOT_RE = re.compile("|".join(re.escape(slot) for slot in _SLOTS))

#: Names that would reveal the arm (its tools and products), masked in every
#: candidate before a judge or a grader sees it (PREREG Amendment 5). A word
#: ends at any non-alphanumeric character, ``_`` included, so the qualified
#: names agents quote (``mcp__docs__assemble_context``) are masked too.
_ARM_WORDS = re.compile(
    r"(?<![a-z0-9])(assemble_context|resolve_url|get_document|fetch_page|search_docs"
    r"|resolve-library-id|get-library-docs|query-docs|web[ _-]?fetch|context7|nina)"
    r"(?![a-z0-9])",
    re.IGNORECASE,
)


def blind(candidate: str) -> str:
    """``candidate`` with every arm-revealing name replaced by ``[tool]``."""
    return _ARM_WORDS.sub("[tool]", candidate)


@dataclass(frozen=True)
class Verdict:
    """One judge ruling; ``correct`` is ``None`` when no reply could be parsed."""

    correct: bool | None
    reason: str
    model: str
    attempts: int
    abstained: bool | None = None  # None when the reply leaves it out


def judge_prompt(task: ExternalTask, candidate: str) -> str:
    """The rubric with the three slots filled in one pass, candidate blinded.

    One pass means text inside the question that looks like a slot is never
    replaced again.
    """
    values = dict(
        zip(
            _SLOTS,
            (
                f"{task.question_title}\n\n{task.question}",
                task.reference_answer,
                blind(candidate),
            ),
            strict=True,
        )
    )
    return _SLOT_RE.sub(lambda m: values[m.group(0)], RUBRIC)


def final_answer(message: str) -> AnswerResult:
    """The agent's answer by PREREG §4.

    It is the last JSON object with an ``answer`` in the final message; without
    one, the whole message is the answer and there are no citations.
    """
    decoder = json.JSONDecoder()
    starts = [i for i, ch in enumerate(message) if ch == "{"]
    for start in reversed(starts):
        try:
            payload, _end = decoder.raw_decode(message, start)
        except ValueError:
            continue
        if isinstance(payload, dict) and isinstance(payload.get("answer"), str):
            return AnswerResult(
                answer=payload["answer"],
                citations=_parse_citations(payload.get("citations")),
            )
    return AnswerResult(answer=message.strip())


def judge(
    task: ExternalTask, candidate: str, provider: LLMProvider, *, retries: int = RETRIES
) -> Verdict:
    """Rule on ``candidate`` with ``provider``, retrying unparseable replies."""
    prompt = judge_prompt(task, candidate)
    for attempt in range(1 + retries):
        reply = provider.complete(
            prompt, seed=DEFAULT_SEED + attempt, response_format="json"
        )
        try:
            payload = json.loads(reply.text)
        except (ValueError, TypeError):
            continue
        if isinstance(payload, dict) and isinstance(payload.get("correct"), bool):
            reason = payload.get("reason")
            abstained = payload.get("abstained")
            return Verdict(
                correct=payload["correct"],
                reason=reason if isinstance(reason, str) else "",
                model=provider.model,
                attempts=attempt + 1,
                abstained=abstained if isinstance(abstained, bool) else None,
            )
    return Verdict(None, "unparseable verdict", provider.model, 1 + retries)


def in_recheck_sample(task_id: str, arm: str, seed: int = SEED) -> bool:
    """Whether a session gets a second primary-judge ruling (about 10%)."""
    digest = hashlib.sha256(f"{seed}:recheck:{task_id}:{arm}".encode()).digest()
    return int.from_bytes(digest[:8], "big") < RECHECK_FRACTION * 2**64


def flip_rate(pairs: Iterable[tuple[bool | None, bool | None]]) -> float:
    """Share of double-judged sessions whose two parseable verdicts differ."""
    usable = [(a, b) for a, b in pairs if a is not None and b is not None]
    return sum(a != b for a, b in usable) / len(usable) if usable else 0.0
