"""``raw_html`` baseline: concatenate the raw snapshot bodies (SPEC §10.3).

The simplest baseline. For each ``ok`` URL on the task it decodes the raw body
stored in the frozen corpus, concatenates them in task order with a small URL
header, and truncates the whole thing to the token budget. No extraction, no
selection — this is the "dump the HTML at the model" strawman.
"""

from __future__ import annotations

from app.fetch.detect import parse_content_type
from bench.corpus_store import CorpusStore
from bench.runners.base import ContextResult, truncate_to_budget
from bench.tasks_schema import Task


class RawHtmlRunner:
    """Concatenate raw snapshot bytes per task URL, truncated to the budget."""

    name = "raw_html"

    def build_context(
        self, task: Task, *, budget_tokens: int, store: CorpusStore
    ) -> ContextResult:
        """Decode and concatenate the raw body of each ``ok`` task URL."""
        parts: list[str] = []
        used: list[str] = []
        for url in task.urls:
            try:
                raw = store.raw_bytes(url)
            except (KeyError, ValueError):
                # URL not in corpus or skipped at snapshot time: ignore it.
                continue
            _media, charset = parse_content_type(store.content_type(url))
            text = raw.decode(charset or "utf-8", errors="replace")
            parts.append(f"[URL] {url}\n{text}")
            used.append(url)

        joined = "\n\n".join(parts)
        truncated, tokens = truncate_to_budget(joined, budget_tokens)
        return ContextResult(text=truncated, context_tokens=tokens, used_urls=used)
