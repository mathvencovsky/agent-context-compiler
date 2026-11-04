"""Extraction on every WCXB page type, not only documentation (phase 2).

Data: the whole WCXB v1.0 at commit ``c039d5ee``, unpacked in ``bench/data/wcxb_full``
(never committed; nothing from it is executed). No network and no API. Uses WCXB's own
dev and test splits. Tune on dev; open test once per change.

    uv run python -m bench.extract_eval.wcxb_types --split dev --out before_dev.json
    uv run python -m bench.extract_eval.wcxb_types --split dev --out after.json \
        --compare before_dev.json

**Acceptance rule for the fallback fix (DECISIONS.md 2026-11-04, written before any fix
was tried).** Paired against the "before" run of the same split, for ``nina`` only:

1. ``article``, ``product``, ``listing``, ``service``, ``collection`` and ``forum``:
   the mean ``without`` (boilerplate that must not appear) falls in at least four of
   the six types, and rises in none by more than 1 point.
2. ``f1`` falls by at most 1 point in any type, and rises overall.
3. ``documentation``: ``recall`` falls by at most 0.5 point, and code and table recall
   fall by at most 1 point.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from bench.extract_eval import metrics as m
from bench.extract_eval.html_eval import (
    RUNNERS,
    Output,
    nina_details,
    page_metrics,
    read_gz,
)

DATA = Path("bench/data/wcxb_full")
NON_DOC = ("article", "product", "listing", "service", "collection", "forum")


def run(split: str) -> list[dict[str, Any]]:  # pragma: no cover - needs the data
    """Every system on every page of ``split``."""
    rows = []
    paths = sorted((DATA / split / "ground-truth").glob("*.json"))
    for done, path in enumerate(paths):
        if done % 100 == 0:
            print(f"{done}/{len(paths)}", flush=True)
        record = json.loads(path.read_text(encoding="utf-8"))
        html = read_gz(DATA / split / "html" / f"{path.stem}.html.gz")
        url = record.get("url") or "https://example.test/"
        row: dict[str, Any] = {
            "id": path.stem,
            "type": record["_internal"]["page_type"]["primary"],
        }
        for name, runner in RUNNERS.items():
            try:
                out, error = runner(html, url), None
            except Exception as exc:  # an extractor crash is a measured failure
                out, error = Output("", "", [], []), type(exc).__name__
            row[name] = {**page_metrics(out, record["ground_truth"]), "error": error}
            if name == "nina" and out.doc is not None:
                row["fallback"] = nina_details(out.doc)["readability_fallback"]
        rows.append(row)
    return rows


def _ratio(cells: list[Mapping[str, Any]], found: str, total: str) -> float | None:
    whole = sum(c[total] for c in cells)
    return sum(c[found] for c in cells) / whole if whole else None


def summarize(rows: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Mean metrics per page type and per system, with an ``all`` row."""
    by_type: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        by_type[row["type"]].append(row)
        by_type["all"].append(row)
    out: dict[str, dict[str, Any]] = {}
    for kind, group in by_type.items():
        out[kind] = {"pages": len(group)}
        out[kind]["fallback"] = m.mean([float(r.get("fallback", False)) for r in group])
        for name in RUNNERS:
            cells = [r[name] for r in group]
            out[kind][name] = {
                key: m.mean([c[key] for c in cells if c[key] is not None])
                for key in ("f1", "recall", "precision", "without", "leak")
            }
            out[kind][name]["code"] = _ratio(cells, "code_text_found", "code_total")
            out[kind][name]["cell"] = _ratio(cells, "cell_found", "cell_total")
    return out


def verdict(
    before: Mapping[str, Mapping[str, Any]], after: Mapping[str, Mapping[str, Any]]
) -> list[tuple[str, bool, str]]:
    """The acceptance rule, one ``(check, passed, detail)`` row per item."""

    def delta(kind: str, key: str) -> float:
        was, now = before[kind]["nina"][key], after[kind]["nina"][key]
        return 0.0 if was is None or now is None else round(now - was, 6)

    fell = [t for t in NON_DOC if delta(t, "without") < 0]
    rose = [t for t in NON_DOC if delta(t, "without") > 0.01]
    kinds = [k for k in before if k != "all"]
    f1_worst = min(delta(k, "f1") for k in kinds)
    doc = "documentation"
    return [
        ("1. boilerplate falls in 4+ types", len(fell) >= 4, ", ".join(fell)),
        ("1. boilerplate rises <= 1 pt", not rose, ", ".join(rose)),
        ("2. f1 falls <= 1 pt in any type", f1_worst >= -0.01, f"{f1_worst:+.3f}"),
        ("2. f1 rises overall", delta("all", "f1") > 0, f"{delta('all', 'f1'):+.3f}"),
        (
            "3. docs recall <= 0.5 pt",
            delta(doc, "recall") >= -0.005,
            f"{delta(doc, 'recall'):+.3f}",
        ),
        (
            "3. docs code <= 1 pt",
            delta(doc, "code") >= -0.01,
            f"{delta(doc, 'code'):+.3f}",
        ),
        (
            "3. docs tables <= 1 pt",
            delta(doc, "cell") >= -0.01,
            f"{delta(doc, 'cell'):+.3f}",
        ),
    ]


def table(summary: Mapping[str, Mapping[str, Any]]) -> str:
    """A Markdown table of F1 and leaked boilerplate per type."""
    lines = [
        "| type | pages | plan B | F1 NINA | F1 trafilatura | F1 readability "
        "| without NINA | without trafilatura |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for kind, row in sorted(summary.items()):
        names = (row["nina"]["f1"], row["trafilatura"]["f1"], row["readability"]["f1"])
        f1s = " | ".join(f"{x:.3f}" for x in names)
        lines.append(
            f"| {kind} | {row['pages']} | {row['fallback']:.0%} | {f1s} "
            f"| {row['nina']['without']:.1%} | {row['trafilatura']['without']:.1%} |"
        )
    return "\n".join(lines)


def main() -> None:  # pragma: no cover - needs the data
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--split", choices=["dev", "test"], default="dev")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--compare", type=Path)
    args = parser.parse_args()
    rows = run(args.split)
    args.out.write_text(json.dumps(rows), encoding="utf-8")
    summary = summarize(rows)
    print(table(summary))
    if args.compare:
        before = summarize(json.loads(args.compare.read_text(encoding="utf-8")))
        for check, passed, detail in verdict(before, summary):
            print(f"{'PASS' if passed else 'FAIL'} {check}: {detail}")


if __name__ == "__main__":
    main()
