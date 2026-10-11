"""LLM provider interface and result type (SPEC §10.3 / §10.4).

The benchmark answers each task with a fixed answerer model and grades the answer
with a *different* judge model (SPEC §10.4 "juiz = modelo diferente do
respondedor"). Both roles are expressed through :class:`LLMProvider`, a
synchronous seam that mirrors the rest of the network stack (``app.fetch`` is
synchronous). Callers inject a provider; tests inject the deterministic fake so
no test touches the network (CLAUDE.md).

Reproducibility (DECISIONS.md 2026-10-11 — "temperatura 0 indisponível"): SPEC
§10.3 asked for temperature 0, but the current OpenAI and Anthropic model
generations have DEPRECATED the temperature parameter (every model tested
rejects it). The benchmark therefore relies on a FIXED prompt plus an optional
fixed ``seed`` (honored by providers that support it) and the bootstrap
confidence intervals in the report (SPEC §10.5) for reproducibility, instead of
temperature 0. ``complete`` takes ``seed``; temperature is not passed.

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


#: Fixed seed used across the benchmark for reproducibility (providers that
#: support a ``seed`` honor it; others ignore it). Replaces temperature 0, which
#: current models no longer accept.
DEFAULT_SEED = 7


@runtime_checkable
class LLMProvider(Protocol):
    """Synchronous completion seam for the benchmark answerer and judge.

    Implementations should be as deterministic as the underlying model allows for
    a given ``(prompt, seed, response_format)`` (the fake is fully deterministic;
    real models use a fixed ``seed`` where supported, since temperature control
    was removed). When ``response_format == "json"`` the returned ``text`` MUST
    be a single valid JSON document.
    """

    #: Stable model identifier (e.g. ``"gpt-6.1-sol"``), surfaced in reports.
    model: str

    def complete(
        self,
        prompt: str,
        *,
        seed: int = DEFAULT_SEED,
        response_format: ResponseFormat = "text",
    ) -> LLMResult:
        """Complete ``prompt`` and return the text plus token accounting."""
        ...
