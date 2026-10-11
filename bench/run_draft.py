"""Human-run wiring to draft benchmark tasks with the REAL answerer provider.

Spends OpenAI tokens (the answerer ``gpt-6.1-sol``). Reads the frozen corpus
under ``bench/corpus/`` (run ``python -m bench.snapshot`` first) and writes the
AI drafts to ``bench/tasks/tasks.yaml`` as ``author="ai_draft"``,
``validated=False`` for a human to review with ``bench/review_tasks.py``.

This is deliberately a separate human entry point (``bench/draft_tasks.main``
refuses to run) so the wiring of real provider + paths is explicit and never
triggered by the test suite.

Usage:
    uv run python -m bench.run_draft
"""

from __future__ import annotations

from pathlib import Path

from app.providers.llm.openai import OpenAIProvider
from bench.corpus_store import CorpusStore
from bench.draft_tasks import draft
from bench.tasks_schema import dump_tasks

_CORPUS_DIR = Path(__file__).resolve().parent / "corpus"
_TASKS_PATH = Path(__file__).resolve().parent / "tasks" / "tasks.yaml"


def main() -> None:
    store = CorpusStore(_CORPUS_DIR)
    provider = OpenAIProvider()
    tasks = draft(store, provider=provider)
    _TASKS_PATH.parent.mkdir(parents=True, exist_ok=True)
    dump_tasks(tasks, _TASKS_PATH)

    insufficient = sum(1 for t in tasks if t.expected == ["INSUFFICIENT"])
    by_category: dict[str, int] = {}
    for t in tasks:
        by_category[t.category] = by_category.get(t.category, 0) + 1
    print(f"drafted {len(tasks)} tasks -> {_TASKS_PATH}")
    print(f"  INSUFFICIENT drafts: {insufficient}")
    print(f"  by category: {by_category}")


if __name__ == "__main__":
    main()
