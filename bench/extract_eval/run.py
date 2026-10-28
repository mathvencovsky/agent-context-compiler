"""Run the extraction eval and compare with the committed baseline.

Usage:
    uv run python -m bench.extract_eval.run                  # dev + PDF, vs baseline
    uv run python -m bench.extract_eval.run --split test     # once per change (rules)
    uv run python -m bench.extract_eval.run --split dev,test --write-baseline

Local files only (run ``bench.extract_eval.fetch`` first); no provider, no
network. The acceptance rules for Phase P live in
``bench/results/extract_eval_baseline.md`` and are fixed.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path
from typing import Any

from bench.extract_eval import html_eval, pdf_eval
from bench.extract_eval import metrics as m
from bench.extract_eval.fetch import PDF_FACTS, WCXB_MANIFEST

BASELINE_JSON = Path("bench/results/extract_eval_baseline.json")
BASELINE_MD = Path("bench/results/extract_eval_baseline.md")
#: Per-page values kept in the JSON, enough for paired comparisons later.
PAGE_KEYS = ("precision", "recall", "f1", "leak", "without", "glued")


def _git(*args: str) -> str:
    try:
        out = subprocess.run(["git", *args], capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return out.stdout.strip()


def provenance() -> dict[str, Any]:
    return {
        "measured_at": dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "git_commit": _git("rev-parse", "HEAD"),
        "app_tree": _git("rev-parse", "HEAD:app"),
        "app_dirty": bool(_git("status", "--porcelain", "--", "app")),
        "versions": {
            pkg: version(pkg)
            for pkg in (
                "trafilatura",
                "readability-lxml",
                "lxml",
                "pdfplumber",
                "pypdfium2",
            )
        },
        "wcxb_manifest_sha256": hashlib.sha256(WCXB_MANIFEST.read_bytes()).hexdigest(),
        "pdf_facts_sha256": hashlib.sha256(PDF_FACTS.read_bytes()).hexdigest(),
        "paid_calls": 0,
    }


def _round(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 4)
    if isinstance(value, (list, tuple)):
        return [_round(v) for v in value]
    if isinstance(value, dict):
        return {k: _round(v) for k, v in value.items()}
    return value


def compact_pages(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        item: dict[str, Any] = {"id": row["id"]}
        for name in html_eval.SYSTEMS:
            item[name] = {k: row[name][k] for k in PAGE_KEYS}
        details = row.get("nina_details", {})
        item["nina_code"] = {
            k: details.get(k)
            for k in ("code_blocks", "code_with_language", "code_lineno_leak",
                      "code_duplicated_in_text", "readability_fallback")
        }  # fmt: skip
        out.append(item)
    return out


def measure(splits: list[str], with_pdf: bool) -> dict[str, Any]:
    result: dict[str, Any] = {"meta": provenance(), "html": {}}
    for split in splits:
        rows = html_eval.evaluate_pages({split})
        result["html"][split] = {
            "summary": html_eval.aggregate(rows),
            "pages": compact_pages(rows),
        }
    if with_pdf:
        result["pdf"] = pdf_eval.evaluate_all()
    rounded: dict[str, Any] = _round(result)
    return rounded


# ------------------------------------------------------------------ reporting


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{100 * x:.1f}%"


def html_table(summary: dict[str, Any]) -> list[str]:
    lines = [
        "| system | F1 [95% CI] | P | R | leak [95% CI] | without | with "
        "| glued tokens (pages) | code in text | code as block | table pages "
        "| table cells |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name in html_eval.SYSTEMS:
        s = summary[name]
        lines.append(
            f"| {name} | {s['f1']:.3f} [{s['f1_ci'][0]:.3f}, {s['f1_ci'][1]:.3f}] "
            f"| {s['precision']:.3f} | {s['recall']:.3f} "
            f"| {_pct(s['leak'])} [{_pct(s['leak_ci'][0])}, {_pct(s['leak_ci'][1])}] "
            f"| {_pct(s['without'])} | {_pct(s['with'])} "
            f"| {s['glued_tokens']} ({s['glued_pages']}) "
            f"| {_pct(s['code_text_recall'])} | {_pct(s['code_struct_recall'])} "
            f"| {s['table_pages_detected']}/{s['table_gt_pages']} "
            f"| {_pct(s['table_cell_recall'])} |"
        )
    c = summary["nina_code"]
    lines += [
        "",
        f"NINA code blocks: {c['code_blocks']}; "
        f"with language {_pct(c['language_rate'])}; "
        f"token-class 'language' (hljs- bug) {c['token_class_language_blocks']}; "
        f"line-number leak {c['lineno_leak_blocks']}; duplicated inside a text block "
        f"{c['duplicated_in_text_blocks']}; readability fallback on "
        f"{c['readability_fallback_pages']}/{summary['pages']} pages.",
    ]
    return lines


def pdf_table(pdf: dict[str, Any]) -> list[str]:
    lines = [
        "| document | license | facts passed | long-token rate | paragraphs/page "
        "| tables (empty) | pypdfium2 text facts | NINA text facts |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for d in pdf["documents"]:
        passed = sum(f["passed"] for f in d["facts"])
        ref = d["reference_pypdfium2"]
        lines.append(
            f"| {d['id']} | {d['license']} | {passed}/{len(d['facts'])} "
            f"| {_pct(d['long_token_rate'])} | {d['paragraphs_per_page']:.1f} "
            f"| {d['tables']} ({d['empty_tables']}) | {_pct(ref['text_fact_recall'])} "
            f"| {_pct(ref['nina_text_fact_recall'])} |"
        )
    s = pdf["summary"]
    lines += [
        "",
        f"All PDFs: {s['facts_passed']}/{s['facts']} facts ({_pct(s['fact_recall'])}); "
        f"by kind {s['by_kind']}; long-token rate {_pct(s['long_token_rate'])}; "
        f"tables {s['tables']}, empty {s['empty_tables']}.",
    ]
    return lines


def render_markdown(result: dict[str, Any]) -> str:
    meta = result["meta"]
    out = [
        f"Measured {meta['measured_at']} at commit `{meta['git_commit'][:12]}`, "
        f"app tree `{meta['app_tree'][:12]}` (dirty: {meta['app_dirty']}). "
        f"Versions: {meta['versions']}. Paid calls: 0.",
        "",
    ]
    for split, block in result["html"].items():
        out += [f"### WCXB-docs, {split} ({block['summary']['pages']} pages)", ""]
        out += html_table(block["summary"]) + [""]
    if "pdf" in result:
        out += ["### PDF facts", ""] + pdf_table(result["pdf"]) + [""]
    return "\n".join(out)


# ------------------------------------------------------------------ comparison


def _delta(label: str, before: Any, after: Any) -> None:
    print(f"  {label:18} {before} -> {after}")


def compare(current: dict[str, Any], baseline: dict[str, Any]) -> list[str]:
    """Paired NINA deltas vs the baseline and the automatic non-regression gates.

    Returns the failed gates (empty list = all automatic gates pass). Targeted
    thresholds (rules H3-H6, P2-P4) depend on what a change aims at and are
    read by the reviewer from the printed deltas.
    """
    failed: list[str] = []
    for split, block in current["html"].items():
        base = baseline["html"].get(split)
        if base is None:
            print(f"[{split}] no baseline for this split")
            continue
        base_pages = {p["id"]: p for p in base["pages"]}
        pairs = [(base_pages[p["id"]]["nina"], p["nina"]) for p in block["pages"]]
        print(f"\n[{split}] NINA vs baseline, paired over {len(pairs)} pages")
        for key in ("f1", "recall", "precision", "leak"):
            d, lo, hi = m.paired_bootstrap_ci(
                [a[key] for a, _ in pairs], [b[key] for _, b in pairs]
            )
            print(f"  {key:9} {d:+.4f} [{lo:+.4f}, {hi:+.4f}]")
        wo = [
            (a["without"], b["without"]) for a, b in pairs if a["without"] is not None
        ]
        d, lo, hi = m.paired_bootstrap_ci([a for a, _ in wo], [b for _, b in wo])
        print(f"  without   {d:+.4f} [{lo:+.4f}, {hi:+.4f}]")
        g0 = sum(a["glued"] for a, _ in pairs)
        g1 = sum(b["glued"] for _, b in pairs)
        print(f"  glued     {g0} -> {g1}")
        s0, s1 = base["summary"]["nina"], block["summary"]["nina"]
        for key in ("code_text_recall", "code_struct_recall", "table_cell_recall"):
            print(f"  {key:18} {_pct(s0[key])} -> {_pct(s1[key])}")
        c0, c1 = base["summary"]["nina_code"], block["summary"]["nina_code"]
        _delta("language_rate", _pct(c0["language_rate"]), _pct(c1["language_rate"]))
        _delta("lineno_leak_blocks", c0["lineno_leak_blocks"], c1["lineno_leak_blocks"])
        # Automatic gates (rules H1, H2, H7 of the baseline summary).
        f1_d, f1_lo, _ = m.paired_bootstrap_ci(
            [a["f1"] for a, _ in pairs], [b["f1"] for _, b in pairs]
        )
        if f1_d < -0.002 or f1_lo < -0.01:
            failed.append(f"H1 [{split}] F1 delta {f1_d:+.4f} (CI low {f1_lo:+.4f})")
        r_d, _, _ = m.paired_bootstrap_ci(
            [a["recall"] for a, _ in pairs], [b["recall"] for _, b in pairs]
        )
        if r_d < -0.005:
            failed.append(f"H2 [{split}] recall delta {r_d:+.4f}")
        for key in ("code_text_recall", "table_cell_recall"):
            if (
                s0[key] is not None
                and s1[key] is not None
                and s1[key] < s0[key] - 0.005
            ):
                failed.append(f"H7 [{split}] {key} {s0[key]:.3f} -> {s1[key]:.3f}")
        if g1 > g0 * 1.10 + 2:
            failed.append(f"H5 [{split}] glued tokens rose {g0} -> {g1}")
    if "pdf" in current and "pdf" in baseline:
        base_facts = {
            (d["id"], f["id"]): f["passed"]
            for d in baseline["pdf"]["documents"]
            for f in d["facts"]
        }
        print("\n[pdf] facts vs baseline")
        for doc in current["pdf"]["documents"]:
            for fact in doc["facts"]:
                before = base_facts.get((doc["id"], fact["id"]))
                if before and not fact["passed"]:
                    failed.append(
                        f"P1 [pdf] regression {doc['id']} {fact['id']}: {fact['fact']}"
                    )
                elif before is False and fact["passed"]:
                    print(f"  newly passing {doc['id']} {fact['id']}: {fact['fact']}")
        b, c = baseline["pdf"]["summary"], current["pdf"]["summary"]
        _delta(
            "facts",
            f"{b['facts_passed']}/{b['facts']}",
            f"{c['facts_passed']}/{c['facts']}",
        )
        _delta(
            "long-token rate", _pct(b["long_token_rate"]), _pct(c["long_token_rate"])
        )
        print(f"  empty tables {b['empty_tables']} -> {c['empty_tables']}")
        if c["empty_tables"] > b["empty_tables"]:
            failed.append("P5 [pdf] more empty tables than the baseline")
    return failed


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - real data
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--split", default="dev", help="dev, test or dev,test")
    parser.add_argument("--no-pdf", action="store_true")
    parser.add_argument("--write-baseline", action="store_true")
    parser.add_argument("--force", action="store_true", help="overwrite the baseline")
    parser.add_argument("--out", type=Path, help="also save this run's JSON here")
    args = parser.parse_args(argv)
    splits = [s.strip() for s in args.split.split(",") if s.strip()]
    if not set(splits) <= {"dev", "test"}:
        parser.error("--split takes dev, test or dev,test")
    if "test" in splits and not args.write_baseline:
        print("NOTE: test is the confirmation split: one look per change (rule C1).")

    result = measure(splits, with_pdf=not args.no_pdf)
    print(render_markdown(result))
    if args.out:
        args.out.write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
    status = 0
    if args.write_baseline:
        if BASELINE_JSON.exists() and not args.force:
            print(f"{BASELINE_JSON} exists; the baseline is fixed (use --force only "
                  "with a recorded reason)")  # fmt: skip
            return 1
        BASELINE_JSON.parent.mkdir(parents=True, exist_ok=True)
        BASELINE_JSON.write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
        print(f"wrote {BASELINE_JSON}")
    elif BASELINE_JSON.exists():
        failed = compare(result, json.loads(BASELINE_JSON.read_text(encoding="utf-8")))
        print("\nautomatic gates:", "PASS" if not failed else "FAIL")
        for line in failed:
            print("  " + line)
        status = 1 if failed else 0
    print("paid calls: 0")
    return status


if __name__ == "__main__":
    sys.exit(main())
