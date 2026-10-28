"""WCXB-docs evaluation: NINA's frozen HTML extractor vs trafilatura/readability.

Per page and per system it computes the metrics of ``metrics.py``; the
aggregate is a mean over pages (macro), except code and table recall, which
are summed over blocks/cells (micro) because most pages have none.
"""

from __future__ import annotations

import gzip
import io
import json
import re
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

import lxml.html
import trafilatura
from readability import Document as ReadabilityDocument

from app.extract.ir import Document
from bench.extract_eval import metrics as m
from bench.extract_eval.fetch import load_wcxb_manifest, wcxb_local
from bench.runners.ours import _extract_ir, _source_for

SYSTEMS = ("nina", "trafilatura", "readability")
MAX_HTML_BYTES = 32 * 1024 * 1024
_MD_INLINE = re.compile(r"[*_`]+")


def read_gz(path: Path, limit: int = MAX_HTML_BYTES) -> str:
    """Decompress a page, refusing anything that inflates past ``limit``."""
    with gzip.GzipFile(fileobj=io.BytesIO(path.read_bytes())) as fh:
        raw = fh.read(limit + 1)
    if len(raw) > limit:
        raise ValueError(f"{path} inflates past {limit} bytes")
    return raw.decode("utf-8", errors="replace")


# ------------------------------------------------------------- system outputs


class Output:
    """What one system produced for one page, in comparable views."""

    def __init__(
        self,
        text: str,
        prose: str,
        code_blocks: list[str],
        table_cells: list[list[str]],
        doc: Document | None = None,
    ) -> None:
        self.text = text  # everything, for F1 / leak / snippets
        self.prose = prose  # text without code, for glued words
        self.code_blocks = code_blocks  # structured code (fenced / <pre> / block)
        self.table_cells = table_cells  # one list of cells per structured table
        self.doc = doc


def run_nina(html: str, url: str) -> Output:
    source, _, _ = _source_for(url, "text/html; charset=utf-8")
    doc = _extract_ir(html.encode("utf-8"), "text/html; charset=utf-8", source)
    return Output(
        text=m.document_text(doc),
        prose="\n".join(b.text for b in doc.blocks if b.type != "code"),
        code_blocks=[m.block_text(b) for b in doc.blocks if b.type == "code"],
        table_cells=[m.block_cells(b) for b in doc.blocks if b.type == "table"],
        doc=doc,
    )


def run_trafilatura(html: str, url: str) -> Output:
    kwargs: dict[str, Any] = {"include_tables": True, "include_comments": False}
    text = trafilatura.extract(html, url=url, **kwargs) or ""
    markdown = (
        trafilatura.extract(html, url=url, output_format="markdown", **kwargs) or ""
    )
    return Output(
        text=text,
        prose=text,
        code_blocks=m.markdown_code_blocks(markdown),
        table_cells=m.markdown_table_cells(markdown),
    )


def run_readability(html: str, url: str) -> Output:
    summary = ReadabilityDocument(html, url=url).summary(html_partial=True)
    tree = lxml.html.fromstring(summary)
    text = tree.text_content() or ""
    tables = [
        [m.collapse(c.text_content()) for c in t.iter("td", "th")]
        for t in tree.iter("table")
    ]
    return Output(
        text=text,
        prose=text,
        code_blocks=[p.text_content() or "" for p in tree.iter("pre")],
        table_cells=tables,
    )


RUNNERS: dict[str, Callable[[str, str], Output]] = {
    "nina": run_nina,
    "trafilatura": run_trafilatura,
    "readability": run_readability,
}


# ------------------------------------------------------------- per page


def page_metrics(out: Output, gt: dict[str, Any]) -> dict[str, Any]:
    """Every metric of one system on one page (ground truth = WCXB record)."""
    ref = gt.get("main_content") or ""
    ref_md = gt.get("main_content_markdown") or ""
    p, r, f1 = m.word_prf(out.text, ref)
    vocab = set(m.tokenize(ref))
    gt_code = m.markdown_code_blocks(ref_md)
    code_text_found, code_total = m.contained(gt_code, out.text)
    code_struct_found, _ = m.contained(gt_code, "\n␞\n".join(out.code_blocks))
    gt_tables = m.markdown_table_cells(ref_md)
    gt_cells = [_MD_INLINE.sub("", c) for t in gt_tables for c in t]
    sys_cells = " ␞ ".join(c for t in out.table_cells for c in t)
    cell_found, cell_total = m.contained(gt_cells, sys_cells, min_chars=3)
    return {
        "precision": p,
        "recall": r,
        "f1": f1,
        "leak": m.leak_rate(out.text, ref),
        "with": m.snippet_rate(out.text, gt.get("with")),
        "without": m.snippet_rate(out.text, gt.get("without")),
        "glued": len(m.glued_tokens(out.prose, vocab)),
        "code_total": code_total,
        "code_text_found": code_text_found,
        "code_struct_found": code_struct_found,
        "gt_tables": len(gt_tables),
        "tables": len(out.table_cells),
        "cell_total": cell_total,
        "cell_found": cell_found,
    }


def nina_details(doc: Document) -> dict[str, Any]:
    """NINA-only structure facts: code languages, line numbers, duplication."""
    codes = [b for b in doc.blocks if b.type == "code"]
    languages = [str(b.data.get("language") or "") for b in codes]
    containers = [b.text for b in doc.blocks if b.type != "code"]
    dup = 0
    for block in codes:
        snippet = m.collapse(m.block_text(block))
        if len(snippet) >= 15 and any(snippet in m.collapse(t) for t in containers):
            dup += 1
    return {
        "code_blocks": len(codes),
        "code_with_language": sum(bool(x) for x in languages),
        "code_token_class_language": sum(x in m.TOKEN_CLASS_NAMES for x in languages),
        "code_lineno_leak": sum(m.has_line_number_leak(m.block_text(b)) for b in codes),
        "code_duplicated_in_text": dup,
        "languages": dict(Counter(x for x in languages if x)),
        "readability_fallback": any(
            w.code == "readability_fallback" for w in doc.warnings
        ),
        "warnings": sorted({w.code for w in doc.warnings}),
    }


def evaluate_pages(splits: set[str]) -> list[dict[str, Any]]:
    """Run every system on every pinned page of ``splits``."""
    rows = []
    for page in load_wcxb_manifest()["pages"]:
        if page["split"] not in splits:
            continue
        html_path, gt_path = wcxb_local(page)
        html = read_gz(html_path)
        gt = json.loads(gt_path.read_text(encoding="utf-8"))["ground_truth"]
        url = page["url"] or "https://example.test/"
        row: dict[str, Any] = {"id": page["id"], "split": page["split"], "url": url}
        for name, runner in RUNNERS.items():
            try:
                out = runner(html, url)
                error = None
            except Exception as exc:  # an extractor crash is a measured failure
                out, error = Output("", "", [], []), type(exc).__name__
            metrics = page_metrics(out, gt)
            metrics["error"] = error
            row[name] = metrics
            if name == "nina" and out.doc is not None:
                row["nina_details"] = nina_details(out.doc)
        rows.append(row)
    return rows


# ------------------------------------------------------------- aggregate


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Split-level summary per system (CIs: seeded page bootstrap)."""
    summary: dict[str, Any] = {"pages": len(rows)}
    for name in SYSTEMS:
        cells = [r[name] for r in rows]

        def col(key: str, cells: list[dict[str, Any]] = cells) -> list[float]:
            return [c[key] for c in cells if c[key] is not None]

        f1, leak, without = col("f1"), col("leak"), col("without")
        code_total = sum(col("code_total"))
        cell_total = sum(col("cell_total"))
        gt_table_pages = [c for c in cells if c["gt_tables"]]
        summary[name] = {
            "f1": m.mean(f1),
            "f1_ci": m.bootstrap_ci(f1),
            "precision": m.mean(col("precision")),
            "recall": m.mean(col("recall")),
            "leak": m.mean(leak),
            "leak_ci": m.bootstrap_ci(leak),
            "with": m.mean(col("with")),
            "without": m.mean(without),
            "without_ci": m.bootstrap_ci(without),
            "glued_tokens": int(sum(col("glued"))),
            "glued_pages": sum(1 for c in cells if c["glued"]),
            "code_gt_blocks": int(code_total),
            "code_text_recall": sum(col("code_text_found")) / code_total
            if code_total
            else None,
            "code_struct_recall": (
                sum(col("code_struct_found")) / code_total if code_total else None
            ),
            "table_gt_pages": len(gt_table_pages),
            "table_pages_detected": sum(1 for c in gt_table_pages if c["tables"]),
            "table_cell_recall": sum(col("cell_found")) / cell_total
            if cell_total
            else None,
            "errors": sum(1 for c in cells if c["error"]),
        }
    details = [r["nina_details"] for r in rows if "nina_details" in r]
    blocks = sum(d["code_blocks"] for d in details)
    languages: Counter[str] = Counter()
    for d in details:
        languages.update(d["languages"])
    summary["nina_code"] = {
        "code_blocks": blocks,
        "language_rate": (sum(d["code_with_language"] for d in details) / blocks)
        if blocks
        else None,
        "token_class_language_blocks": sum(
            d["code_token_class_language"] for d in details
        ),
        "lineno_leak_blocks": sum(d["code_lineno_leak"] for d in details),
        "duplicated_in_text_blocks": sum(d["code_duplicated_in_text"] for d in details),
        "top_languages": dict(languages.most_common(10)),
        "readability_fallback_pages": sum(d["readability_fallback"] for d in details),
    }
    return summary
