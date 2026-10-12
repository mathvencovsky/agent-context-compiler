"""One-off: fix task categories by URL signal and assign a 70/30 dev/test split.

The AI drafts over-assigned ``guide`` and left every task on the ``dev`` split.
This human-run utility (NOT part of the test suite) rewrites each validated task's
``category`` from a deterministic URL->category map (reviewed against the frozen
corpus) and then stratifies a reproducible 70/30 dev/test split per category.

It PREVIEWS the before/after and only writes when run with ``--apply``.

Usage:
    uv run python -m bench.recategorize_split            # preview only
    uv run python -m bench.recategorize_split --apply    # write tasks.yaml
"""

from __future__ import annotations

import collections
import sys
from pathlib import Path

from bench.tasks_schema import Category, Task, dump_tasks, load_tasks

_TASKS_PATH = Path(__file__).resolve().parent / "tasks" / "tasks.yaml"

#: Fraction of each category reserved for the (held-out) test split.
_TEST_FRACTION = 0.30
#: Deterministic seed-free split: tasks are ordered by id and every Nth goes to
#: test, so the split is reproducible and stratified within each category.


def _category_for_url(url: str) -> Category:
    """Map a corpus URL to its canonical SPEC §10.2 category (reviewed by hand)."""
    u = url.lower()
    # changelog / release notes.
    if any(
        s in u
        for s in ("/changes/", "release-notes", "/whatsnew/", "react-19", "/releases")
    ):
        return "changelog"
    # PDFs are the RFC specs.
    if u.endswith(".pdf"):
        return "pdf"
    # parameter / configuration / reference tables.
    if any(
        s in u
        for s in (
            "/config",
            "runtime-config",
            "/pragma",
            "configmap",
            "/http/headers",
            "/http/status",
        )
    ):
        return "params_table"
    # API references.
    if any(
        s in u
        for s in (
            "/api/",
            "/api/core/",
            "reference/react",
            "library/functions",
            "/c3ref/",
            "functions.html",
            "/kubectl/",
            "reference/cli/",
            "global_objects/promise",
            "/http/methods",
            "lang_select",
            "lang_createtable",
            "sql-select",
            "/reference/expressions",
            "rfc9110.html",
            "rfc9111.html",
            "asyncio-task",
            "using_fetch",
        )
    ):
        return "api_reference"
    # everything else is a guide/tutorial/overview.
    return "guide"


def _assign_splits(tasks: list[Task]) -> list[Task]:
    """Return tasks with a stratified, deterministic 70/30 dev/test split.

    Within each category tasks are ordered by id; a fixed stride sends ~30% to
    ``test`` spread evenly, so the split is reproducible and representative.
    """
    by_cat: dict[str, list[Task]] = collections.defaultdict(list)
    for t in tasks:
        by_cat[t.category].append(t)

    out: list[Task] = []
    for _cat, group in by_cat.items():
        group_sorted = sorted(group, key=lambda t: t.id)
        n_test = round(len(group_sorted) * _TEST_FRACTION)
        if n_test == 0 and len(group_sorted) >= 2:
            n_test = 1  # ensure small categories still contribute to test
        # Pick evenly spaced test indices for a representative hold-out.
        test_idx = set()
        if n_test:
            stride = len(group_sorted) / n_test
            test_idx = {int(i * stride) for i in range(n_test)}
        for i, t in enumerate(group_sorted):
            split = "test" if i in test_idx else "dev"
            out.append(t.model_copy(update={"split": split}))
    return sorted(out, key=lambda t: t.id)


def main() -> None:
    apply = "--apply" in sys.argv
    tasks = load_tasks(_TASKS_PATH)

    before_cat = collections.Counter(t.category for t in tasks)
    recategorized = [
        t.model_copy(update={"category": _category_for_url(t.urls[0])}) for t in tasks
    ]
    after_cat = collections.Counter(t.category for t in recategorized)
    final = _assign_splits(recategorized)
    split_count = collections.Counter(t.split for t in final)
    split_by_cat = collections.Counter((t.category, t.split) for t in final)

    print("category BEFORE:", dict(before_cat))
    print("category AFTER :", dict(after_cat))
    print("split          :", dict(split_count))
    print("split by category:")
    for cat in sorted({t.category for t in final}):
        dev_n = split_by_cat[(cat, "dev")]
        test_n = split_by_cat[(cat, "test")]
        print(f"  {cat:14s} dev={dev_n:3d}  test={test_n:3d}")
    changed = sum(
        1 for a, b in zip(tasks, recategorized, strict=True) if a.category != b.category
    )
    print(f"\ncategory changed on {changed}/{len(tasks)} tasks")

    if apply:
        dump_tasks(final, _TASKS_PATH)
        print(f"\nWROTE {_TASKS_PATH}")
    else:
        print("\n(preview only — rerun with --apply to write)")


if __name__ == "__main__":
    main()
