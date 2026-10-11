"""Context-building strategies for the benchmark (SPEC §10.3).

Every strategy ("runner") reads from the frozen corpus store and turns a task's
URLs into a bounded context string the answerer sees. The baselines live here:
``raw_html``, ``naive_text`` and ``naive_rag``; ``firecrawl`` is optional and
auto-skips without an API key. The product strategy ``ours``/``ours_*`` is M7 and
is NOT implemented in this milestone.
"""

from __future__ import annotations

from bench.runners.base import ContextResult, Runner, available_runners, get_runner

__all__ = ["ContextResult", "Runner", "available_runners", "get_runner"]
