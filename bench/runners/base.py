"""Runner interface, token truncation, and registry (SPEC §10.3).

A :class:`Runner` turns one task's corpus URLs into a bounded context string the
answerer sees. All strategies read ONLY from the frozen
:class:`~bench.corpus_store.CorpusStore` (SPEC §10.1) — never the live web — and
truncate the result deterministically to a token budget using
``app.index.tokens.count_tokens`` (cl100k_base), so two runs over the same corpus
produce byte-identical context (SPEC §10 determinism).

The registry (:func:`get_runner` / :func:`available_runners`) is populated lazily
from the baseline modules; ``firecrawl`` registers itself only when a key is
present (SPEC §10.3). ``ours``/``ours_*`` (M7) run NINA's real pipeline over the
frozen snapshot and are registered here too.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from app.index.tokens import count_tokens
from bench.corpus_store import CorpusStore
from bench.tasks_schema import Task

if TYPE_CHECKING:
    from app.providers.embeddings.base import EmbeddingProvider
    from app.providers.rerank.base import RerankProvider


@dataclass(frozen=True)
class ContextResult:
    """The bounded context a runner built for a task.

    ``text`` is the context handed to the answerer; ``context_tokens`` is its
    cl100k_base token count (always ``<= budget_tokens`` the runner was given);
    ``used_urls`` are the task URLs that actually contributed text, in order.
    """

    text: str
    context_tokens: int
    used_urls: list[str]


@runtime_checkable
class Runner(Protocol):
    """A context-building strategy over the frozen corpus (SPEC §10.3)."""

    #: Stable strategy name used in the CLI and the report (e.g. ``"raw_html"``).
    name: str

    def build_context(
        self, task: Task, *, budget_tokens: int, store: CorpusStore
    ) -> ContextResult:
        """Build a context for ``task`` within ``budget_tokens`` from ``store``."""
        ...


def truncate_to_budget(text: str, budget_tokens: int) -> tuple[str, int]:
    """Return ``text`` cut to at most ``budget_tokens`` tokens, with its count.

    Truncation is deterministic: the cl100k_base token ids of ``text`` are cut to
    the budget and decoded back, so the same input always yields the same output.
    A non-positive budget yields ``("", 0)``.
    """
    if budget_tokens <= 0 or not text:
        return "", 0
    from app.index.tokens import _encoding  # local import keeps the seam private

    encoding = _encoding()
    token_ids = encoding.encode(text)
    if len(token_ids) <= budget_tokens:
        return text, len(token_ids)
    kept = token_ids[:budget_tokens]
    return encoding.decode(kept), len(kept)


#: Registry of runner factories, filled on first lookup to avoid import cycles.
_REGISTRY: dict[str, Runner] = {}


def _ensure_registered(
    *,
    embeddings: EmbeddingProvider | None = None,
    reranker: RerankProvider | None = None,
    lambda_: float | None = None,
    min_value: float | None = None,
) -> None:
    """Populate the registry from the baseline modules (idempotent).

    ``embeddings``/``reranker`` are injected ONLY into the ``ours*`` runners
    (M8 Stage 2 real run); the default ``None`` keeps the deterministic fakes,
    so every test and the fake-provider path are unchanged. ``lambda_``/
    ``min_value`` override the ``ours*`` selection knobs (the Stage 2 run passes
    the frozen config values); ``None`` keeps the ``make_ours_runners`` defaults.
    """
    if _REGISTRY:
        return
    from bench.runners.firecrawl import make_firecrawl_runner
    from bench.runners.naive_rag import NaiveRagRunner
    from bench.runners.naive_text import NaiveTextRunner
    from bench.runners.ours import make_ours_runners
    from bench.runners.raw_html import RawHtmlRunner

    for runner in (RawHtmlRunner(), NaiveTextRunner(), NaiveRagRunner()):
        _REGISTRY[runner.name] = runner

    # M7: the full NINA pipeline (``ours``) plus its five ablation variants.
    # M8 Stage 2: the real retrieval providers + frozen selection knobs are
    # injected here when present (default None = fakes / module defaults).
    ours_kwargs: dict[str, object] = {"embeddings": embeddings, "reranker": reranker}
    if lambda_ is not None:
        ours_kwargs["lambda_"] = lambda_
    if min_value is not None:
        ours_kwargs["min_value"] = min_value
    for ours_runner in make_ours_runners(**ours_kwargs):  # type: ignore[arg-type]
        _REGISTRY[ours_runner.name] = ours_runner

    # firecrawl auto-skips (returns None) when FIRECRAWL_API_KEY is absent
    # (SPEC §10.3): it is simply not registered, so the CLI never offers it.
    firecrawl = make_firecrawl_runner()
    if firecrawl is not None:
        _REGISTRY[firecrawl.name] = firecrawl


def register_ours(
    *,
    embeddings: EmbeddingProvider | None = None,
    reranker: RerankProvider | None = None,
    lambda_: float | None = None,
    min_value: float | None = None,
) -> None:
    """Rebuild the registry with REAL retrieval providers wired into ``ours*``.

    The M8 Stage 2 real run calls this once (``bench.run.run(real=True)``) so the
    ``ours``/``ours_*`` runners use the injected OpenAI embeddings + Jina
    reranker and the FROZEN selection knobs instead of the fakes/defaults. Clears
    the registry first so a previous fake-wired state is replaced. Tests never
    call this (the default path builds the fakes), so no test touches the
    network.
    """
    _REGISTRY.clear()
    _ensure_registered(
        embeddings=embeddings,
        reranker=reranker,
        lambda_=lambda_,
        min_value=min_value,
    )


def get_runner(name: str) -> Runner:
    """Return the registered runner called ``name`` or raise a clear error."""
    _ensure_registered()
    try:
        return _REGISTRY[name]
    except KeyError as exc:
        known = ", ".join(sorted(_REGISTRY)) or "<none>"
        raise KeyError(
            f"unknown runner {name!r}; available strategies: {known}"
        ) from exc


def available_runners() -> list[str]:
    """Return the names of all currently registered runners, sorted."""
    _ensure_registered()
    return sorted(_REGISTRY)


def _count(text: str) -> int:
    """Thin wrapper over ``count_tokens`` for runners that need a raw count."""
    return count_tokens(text)
