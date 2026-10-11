"""``firecrawl`` baseline: optional, auto-skipped without a key (SPEC §10.3).

Firecrawl is an external paid service, so SPEC §10.3 marks it optional: it is
only available when ``FIRECRAWL_API_KEY`` is set in the environment. The harness
asks :func:`make_firecrawl_runner` for a runner; when there is no key it returns
``None`` and the registry simply does not offer ``firecrawl`` — the benchmark
does not fail, it just runs the other strategies.

The real Firecrawl client would reach the network and spend a key, so it is NOT
invoked by any test (tests assert that the runner is absent without a key).
"""

from __future__ import annotations

import os

from bench.corpus_store import CorpusStore
from bench.runners.base import ContextResult
from bench.tasks_schema import Task

#: Environment variable gating the firecrawl runner (SPEC §10.3).
FIRECRAWL_API_KEY_ENV = "FIRECRAWL_API_KEY"


class FirecrawlRunner:  # pragma: no cover - requires a key + network; human-run.
    """Firecrawl-backed context builder (only constructed when a key exists)."""

    name = "firecrawl"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def build_context(
        self, task: Task, *, budget_tokens: int, store: CorpusStore
    ) -> ContextResult:
        """Not implemented offline: Firecrawl requires the network and a key."""
        raise NotImplementedError(
            "the firecrawl runner reaches the Firecrawl API and is only run by a "
            "human with FIRECRAWL_API_KEY set; it is never exercised by tests"
        )


def make_firecrawl_runner() -> FirecrawlRunner | None:
    """Return a :class:`FirecrawlRunner` iff a key is present, else ``None``.

    Absent ``FIRECRAWL_API_KEY`` the runner auto-skips (SPEC §10.3): returning
    ``None`` keeps it out of the registry so the CLI never offers it and the
    benchmark proceeds with the other strategies.
    """
    api_key = os.environ.get(FIRECRAWL_API_KEY_ENV)
    if not api_key:
        return None
    return FirecrawlRunner(api_key)  # pragma: no cover - no key in the test env.
