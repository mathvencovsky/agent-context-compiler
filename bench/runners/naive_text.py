"""``naive_text`` baseline: trafilatura plain-text extraction (SPEC §10.3).

A step up from ``raw_html``: run each task URL's snapshot HTML through
``trafilatura.extract`` to get readable plain text (boilerplate removed), then
concatenate per URL and truncate to the token budget. Still no retrieval — the
whole extracted text is used in order until the budget runs out.

trafilatura 2.x is Apache-2.0 (recorded in DECISIONS.md); it is used ONLY in the
benchmark baselines, never in the product extraction path (CLAUDE.md).
"""

from __future__ import annotations

import trafilatura

from app.fetch.detect import parse_content_type
from bench.corpus_store import CorpusStore
from bench.runners.base import ContextResult, truncate_to_budget
from bench.tasks_schema import Task


def extract_text(html: str) -> str:
    """Return trafilatura's plain-text extraction of ``html`` (``""`` if none).

    ``fast=True`` (no model fallback) keeps the output deterministic for a given
    input; trafilatura returns ``None`` when it finds no main text.
    """
    extracted = trafilatura.extract(html, fast=True)
    return extracted or ""


class NaiveTextRunner:
    """Concatenate trafilatura plain text per task URL, truncated to the budget."""

    name = "naive_text"

    def build_context(
        self, task: Task, *, budget_tokens: int, store: CorpusStore
    ) -> ContextResult:
        """Extract readable text from each ``ok`` task URL and concatenate it."""
        parts: list[str] = []
        used: list[str] = []
        for url in task.urls:
            try:
                raw = store.raw_bytes(url)
            except (KeyError, ValueError):
                continue
            _media, charset = parse_content_type(store.content_type(url))
            html = raw.decode(charset or "utf-8", errors="replace")
            text = extract_text(html)
            if not text:
                continue
            parts.append(f"[URL] {url}\n{text}")
            used.append(url)

        joined = "\n\n".join(parts)
        truncated, tokens = truncate_to_budget(joined, budget_tokens)
        return ContextResult(text=truncated, context_tokens=tokens, used_urls=used)
