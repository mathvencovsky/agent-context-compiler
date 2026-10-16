"""Per-site text retention of NINA's extraction on the frozen corpus (B1).

For every HTML page in the corpus, retention = characters of NINA's Markdown /
characters of the page's visible text (scripts, styles, nav, header and footer
removed), both whitespace-normalized. Per site it reports the median and the
pages keeping under half their text. ``--check`` compares with the committed
baseline and fails on a regression, so an extractor change that starts cutting
a site's content (the PostgreSQL case of M10a item 4) is caught.

Local files only; no network, no provider.

Usage:
    uv run python -m bench.extract_retention            # print the table
    uv run python -m bench.extract_retention --check    # compare with baseline
    uv run python -m bench.extract_retention --write    # refresh the baseline
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import lxml.html

from app.extract.ir import normalize_text
from app.extract.to_markdown import to_markdown
from bench.corpus_store import CorpusStore
from bench.m10a_diagnose import _extract_ir, _source_for
from bench.snapshot import DEFAULT_CORPUS_DIR

BASELINE = Path("bench/extract_retention_baseline.json")
#: Allowed drop of a site's median retention before ``--check`` fails.
MEDIAN_TOLERANCE = 0.02
_NOT_TEXT = "//script|//style|//noscript|//template|//nav|//header|//footer"


def retention(raw: bytes, markdown: str) -> float:
    """Characters NINA kept over the page's visible characters."""
    tree = lxml.html.fromstring(raw)
    for el in tree.xpath(_NOT_TEXT):
        el.drop_tree()
    visible = len(normalize_text(tree.text_content()))
    return len(normalize_text(markdown)) / max(visible, 1)


def summarize(ratios: dict[str, list[float]]) -> dict[str, dict[str, Any]]:
    return {
        site: {
            "pages": len(rs),
            "median": round(statistics.median(rs), 3),
            "under_half": sum(r < 0.5 for r in rs),
        }
        for site, rs in sorted(ratios.items())
    }


def regressions(
    now: dict[str, dict[str, Any]], baseline: dict[str, dict[str, Any]]
) -> list[str]:
    """Sites whose median dropped or whose pages under half grew."""
    found = []
    for site, base in baseline.items():
        cur = now.get(site)
        if cur is None:
            found.append(f"{site}: missing from the corpus")
            continue
        if cur["median"] < base["median"] - MEDIAN_TOLERANCE:
            found.append(f"{site}: median {base['median']} -> {cur['median']}")
        if cur["under_half"] > base["under_half"]:
            found.append(
                f"{site}: pages under half {base['under_half']} -> {cur['under_half']}"
            )
    return found


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - real corpus
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check", action="store_true")
    group.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)

    store = CorpusStore(DEFAULT_CORPUS_DIR)
    ratios: dict[str, list[float]] = defaultdict(list)
    for entry in store.entries():
        content_type = store.content_type(entry.url) if entry.status == "ok" else ""
        if "html" not in content_type:
            continue
        raw = store.raw_bytes(entry.url)
        source, _, _ = _source_for(entry.url, content_type)
        markdown = to_markdown(_extract_ir(raw, content_type, source))
        ratios[entry.url.split("/")[2]].append(retention(raw, markdown))
    now = summarize(ratios)
    for site, row in now.items():
        print(f"{site:32} pages {row['pages']:4} median {row['median']:.3f} "
              f"under half {row['under_half']}")  # fmt: skip
    if args.write:
        BASELINE.write_text(json.dumps(now, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {BASELINE}")
    if args.check:
        found = regressions(now, json.loads(BASELINE.read_text(encoding="utf-8")))
        print("\n".join(found) or "no regression")
        return 1 if found else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
