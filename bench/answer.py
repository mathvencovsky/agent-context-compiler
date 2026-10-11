"""Answer a benchmark task from a built context (SPEC §10.3).

Every strategy's context is handed to the SAME answerer model at temperature 0
with a FIXED prompt (SPEC §10.3 "mesmo LLM responde, temperatura 0, prompt
fixo"), so differences in the results come from the context, not the prompt. The
model must answer ONLY from the given context and return a single JSON document:

``{"answer": str, "citations": [{"url": str, "quote": str}]}``

When the context does not contain the answer the model returns the sentinel
answer ``"INSUFFICIENT"`` (SPEC §10.4 unanswerable handling). The provider is
injected; tests pass the deterministic fake (which emits exactly this shape for
``response_format="json"``), so no test touches the network.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from app.providers.llm.base import LLMProvider
from bench.tasks_schema import Task

#: Sentinel answer meaning the context did not support an answer (SPEC §10.4).
INSUFFICIENT = "INSUFFICIENT"

#: The exact JSON contract the answerer must return (kept short for line length).
_JSON_SHAPE = (
    '{"answer": "<your answer or INSUFFICIENT>", '
    '"citations": [{"url": "<source url>", "quote": "<verbatim quote>"}]}'
)

#: Fixed answerer prompt (SPEC §10.3). Only the question and context vary; the
#: instructions and the JSON contract are constant across every task/strategy.
_PROMPT_TEMPLATE = """You answer a question using ONLY the provided context.

Rules:
- Use only facts found in the context below. Do not use outside knowledge.
- If the context does not contain enough information to answer, set "answer" to
  the exact string "INSUFFICIENT".
- Every citation quote MUST be copied verbatim from the context.

Return a single JSON object with this exact shape:
{json_shape}

Question:
{question}

Context:
{context}
"""


@dataclass(frozen=True)
class Citation:
    """A single supporting citation returned by the answerer."""

    url: str
    quote: str


@dataclass(frozen=True)
class AnswerResult:
    """The parsed answerer output plus the token accounting for pricing.

    ``is_insufficient`` is ``True`` when the model answered with the
    :data:`INSUFFICIENT` sentinel (SPEC §10.4).
    """

    answer: str
    citations: list[Citation] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def is_insufficient(self) -> bool:
        """Whether the answer is the INSUFFICIENT sentinel."""
        return self.answer == INSUFFICIENT


def build_prompt(task: Task, context_text: str) -> str:
    """Render the FIXED answerer prompt for ``task`` over ``context_text``."""
    return _PROMPT_TEMPLATE.format(
        json_shape=_JSON_SHAPE, question=task.question, context=context_text
    )


def _parse_citations(raw: object) -> list[Citation]:
    """Parse the ``citations`` field defensively into :class:`Citation` objects."""
    if not isinstance(raw, list):
        return []
    citations: list[Citation] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        quote = item.get("quote")
        if isinstance(url, str) and isinstance(quote, str):
            citations.append(Citation(url=url, quote=quote))
    return citations


def answer(task: Task, context_text: str, *, provider: LLMProvider) -> AnswerResult:
    """Answer ``task`` from ``context_text`` with ``provider`` (temperature 0).

    Calls ``provider.complete`` with the fixed prompt and ``response_format=
    "json"``, then parses ``{"answer": str, "citations": [...]}``. A missing or
    non-string ``answer``, or output that is not valid JSON, is treated as
    :data:`INSUFFICIENT` (fail explicit — the harness never guesses an answer).
    """
    prompt = build_prompt(task, context_text)
    result = provider.complete(prompt, response_format="json")

    try:
        payload = json.loads(result.text)
    except (ValueError, TypeError):
        payload = None

    if isinstance(payload, dict) and isinstance(payload.get("answer"), str):
        answer_text = payload["answer"]
        citations = _parse_citations(payload.get("citations"))
    else:
        answer_text = INSUFFICIENT
        citations = []

    return AnswerResult(
        answer=answer_text,
        citations=citations,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
    )
