"""LLM provider interface and result type (SPEC §10.3 / §10.4).

The benchmark answers each task with a fixed answerer model at temperature 0 and
grades the answer with a *different* judge model (SPEC §10.3 "mesmo LLM responde,
temperatura 0, prompt fixo"; §10.4 "juiz = modelo diferente do respondedor").
Both roles are expressed through :class:`LLMProvider`, a synchronous seam that
mirrors the rest of the network stack (``app.fetch`` is synchronous). Callers
inject a provider; tests inject the deterministic fake so no test touches the
network (CLAUDE.md).

:class:`LLMResult` carries the token counts the report needs to compute cost per
task from ``bench/prices.yaml`` (SPEC §10.5).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

#: The two response shapes a provider must support. ``"text"`` returns the model
#: output verbatim; ``"json"`` instructs the provider to return a single valid
#: JSON document (the answerer/judge contracts in ``bench/answer.py`` and
#: ``bench/grade.py``).
ResponseFormat = Literal["text", "json"]


@dataclass(frozen=True)
class LLMResult:
    """Outcome of a single ``complete`` call.

    ``input_tokens``/``output_tokens`` are measured with
    ``app.index.tokens.count_tokens`` (cl100k_base) so the report can price a run
    deterministically from ``bench/prices.yaml`` (SPEC §10.5).
    """

    text: str
    input_tokens: int
    output_tokens: int
    model: str


@runtime_checkable
class LLMProvider(Protocol):
    """Synchronous completion seam for the benchmark answerer and judge.

    Implementations must be deterministic for a given ``(prompt, temperature,
    response_format)`` when the underlying model is (the fake is; real models are
    at ``temperature=0``). When ``response_format == "json"`` the returned
    ``text`` MUST be a single valid JSON document.
    """

    #: Stable model identifier (e.g. ``"gpt-6.1-sol"``), surfaced in reports.
    model: str

    def complete(
        self,
        prompt: str,
        *,
        temperature: float = 0.0,
        response_format: ResponseFormat = "text",
    ) -> LLMResult:
        """Complete ``prompt`` and return the text plus token accounting."""
        ...
