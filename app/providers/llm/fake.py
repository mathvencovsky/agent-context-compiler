"""Deterministic fake LLM provider — backs every benchmark test (CLAUDE.md).

The fake derives its output purely from ``sha256(f"{model}|{response_format}|
{prompt}")`` so two calls with the same inputs return byte-identical results
(SPEC §10 acceptance criterion (b): determinism) and never touch the network.

For ``response_format == "json"`` it emits a single valid JSON document in the
generic shape the harness callers expect:

* answerer (``bench/answer.py``):
  ``{"answer": <derived>, "citations": []}``
* judge (``bench/grade.py``):
  ``{"correct": <derived bool>, "reason": <derived>}``

The prompt decides which shape to emit: a prompt that reads like a judging
rubric (it asks the model to decide correctness) yields the judge shape;
anything else yields the answerer shape. Both are valid JSON regardless, so a
caller that parses either shape succeeds.
"""

from __future__ import annotations

import hashlib
import json

from app.index.tokens import count_tokens
from app.providers.llm.base import DEFAULT_SEED, LLMResult, ResponseFormat

#: A small deterministic word pool; the hash selects a slice to build text.
_WORDS = (
    "alpha",
    "bravo",
    "charlie",
    "delta",
    "echo",
    "foxtrot",
    "golf",
    "hotel",
    "india",
    "juliet",
    "kilo",
    "lima",
)


def _digest(model: str, response_format: ResponseFormat, prompt: str) -> bytes:
    """Return the sha256 digest the fake output is derived from."""
    payload = f"{model}|{response_format}|{prompt}".encode()
    return hashlib.sha256(payload).digest()


def _derive_text(digest: bytes) -> str:
    """Build a deterministic space-separated phrase from ``digest``."""
    # Length 3..6 words, each chosen from the pool by a digest byte.
    count = 3 + (digest[0] % 4)
    words = [_WORDS[digest[i + 1] % len(_WORDS)] for i in range(count)]
    return " ".join(words)


def _looks_like_judge_prompt(prompt: str) -> bool:
    """Heuristic: does ``prompt`` ask the model to judge correctness?

    The judge prompt (``bench/grade.py``) asks for a correctness verdict; the
    answerer prompt asks a question about the context. Matching on the judge
    vocabulary keeps the fake's JSON shape aligned with the caller without the
    fake importing the harness.
    """
    lowered = prompt.lower()
    return "correct" in lowered and ("judge" in lowered or "rubric" in lowered)


#: Marker the draft prompt (``bench/draft_tasks.py``) always contains.
_DRAFT_EXCERPT_MARKER = "Excerpt:\n"


def _looks_like_draft_prompt(prompt: str) -> bool:
    """Heuristic: does ``prompt`` ask the model to draft a benchmark question?

    The draft prompt embeds the source block after an ``Excerpt:`` marker and
    asks for a ``{question, answer, quote}`` object. The fake answers it with a
    quote taken LITERALLY from that excerpt, so the draft path (which discards
    unverifiable quotes) is exercised rather than always failing.
    """
    return "draft" in prompt.lower() and _DRAFT_EXCERPT_MARKER in prompt


def _draft_json(prompt: str, digest: bytes, derived: str) -> str:
    """Build a draft-shaped JSON object with a quote literal to the excerpt.

    An unanswerable draft (the prompt asks for an answer NOT in the excerpt)
    yields ``answer="INSUFFICIENT"`` and an empty quote; an answerable one yields
    a verbatim substring of the excerpt as the quote so
    ``bench.draft_tasks`` can verify it.
    """
    excerpt = prompt.split(_DRAFT_EXCERPT_MARKER, 1)[1].strip()
    if "insufficient" in prompt.lower():
        payload: dict[str, object] = {
            "question": f"What unlisted detail about {derived} is omitted here?",
            "answer": "INSUFFICIENT",
            "quote": "",
        }
    else:
        # A deterministic verbatim window of the excerpt (>= 12 chars so the
        # draft generator's quote check accepts it).
        start = digest[0] % max(1, len(excerpt) - 40) if len(excerpt) > 52 else 0
        quote = excerpt[start : start + 40].strip() or excerpt[:40]
        payload = {
            "question": f"Which specific value does this {derived} section state?",
            "answer": quote.split()[0] if quote.split() else derived,
            "quote": quote,
        }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


class FakeLLMProvider:
    """Deterministic, network-free :class:`~app.providers.llm.base.LLMProvider`."""

    def __init__(self, model: str = "fake") -> None:
        self.model = model

    def complete(
        self,
        prompt: str,
        *,
        seed: int = DEFAULT_SEED,
        response_format: ResponseFormat = "text",
    ) -> LLMResult:
        """Return a deterministic result derived from the prompt hash.

        ``seed`` is accepted for interface parity but does not affect the output:
        the fake is always fully deterministic from the prompt.
        """
        digest = _digest(self.model, response_format, prompt)
        derived = _derive_text(digest)

        if response_format == "json":
            if _looks_like_draft_prompt(prompt):
                text = _draft_json(prompt, digest, derived)
            elif _looks_like_judge_prompt(prompt):
                payload: dict[str, object] = {
                    "correct": bool(digest[0] & 1),
                    "reason": derived,
                }
                text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
            else:
                payload = {"answer": derived, "citations": []}
                text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        else:
            text = derived

        return LLMResult(
            text=text,
            input_tokens=count_tokens(prompt),
            output_tokens=count_tokens(text),
            model=self.model,
        )
