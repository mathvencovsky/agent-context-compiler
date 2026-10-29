"""PDF facts evaluation of NINA's frozen PDF extractor.

For each pinned PDF: fact pass/fail (``pdf_facts.yaml``), long-token (glued
words) rate, paragraphs per page, table counts and empty tables. pypdfium2's
plain text (BSD-3/Apache-2.0, installed with pdfplumber) is reported as a
reference row for the facts a plain-text extractor can meet; it is not a
competitor and not part of any acceptance rule.
"""

from __future__ import annotations

import time
from collections import Counter
from typing import Any

import pypdfium2

from bench.extract_eval import metrics as m
from bench.extract_eval.fetch import load_pdf_facts, pdf_local
from bench.runners.ours import _extract_ir, _source_for

_PDF = "application/pdf"


def reference_text(raw: bytes) -> str:
    pdf = pypdfium2.PdfDocument(raw)
    try:
        return "\n".join(page.get_textpage().get_text_range() for page in pdf)
    finally:
        pdf.close()


def evaluate_document(spec: dict[str, Any]) -> dict[str, Any]:
    raw = pdf_local(spec).read_bytes()
    source, _, _ = _source_for(f"https://example.test/{spec['id']}.pdf", _PDF)
    started = time.perf_counter()
    doc = _extract_ir(raw, _PDF, source)
    seconds = time.perf_counter() - started
    facts = m.evaluate_facts(doc, spec["facts"])
    prose = "\n".join(b.text for b in doc.blocks if b.type not in ("code", "table"))
    long_n, words = m.long_token_stats(prose)
    ref = reference_text(raw)
    ref_facts = m.evaluate_text_facts(ref, spec["facts"])
    ref_long, ref_words = m.long_token_stats(ref)
    return {
        "id": spec["id"],
        "license": spec["license"],
        "pages": len({b.page for b in doc.blocks}),
        "blocks": dict(Counter(b.type for b in doc.blocks)),
        "facts": facts,
        "fact_recall": sum(f["passed"] for f in facts) / len(facts),
        "long_tokens": long_n,
        "words": words,
        "long_token_rate": long_n / words if words else 0.0,
        "paragraphs_per_page": m.paragraphs_per_page(doc),
        "tables": sum(1 for b in doc.blocks if b.type == "table"),
        "empty_tables": sum(1 for b in doc.blocks if m.is_empty_table(b)),
        "warnings": dict(Counter(w.code for w in doc.warnings)),
        "seconds": round(seconds, 2),
        "reference_pypdfium2": {
            "text_fact_recall": (
                sum(f["passed"] for f in ref_facts) / len(ref_facts)
                if ref_facts
                else None
            ),
            "nina_text_fact_recall": _text_recall(facts),
            "long_token_rate": ref_long / ref_words if ref_words else 0.0,
        },
    }


def _text_recall(facts: list[dict[str, Any]]) -> float | None:
    rows = [f for f in facts if f["kind"] in ("present", "order")]
    return sum(f["passed"] for f in rows) / len(rows) if rows else None


def evaluate_all() -> dict[str, Any]:
    docs = [evaluate_document(spec) for spec in load_pdf_facts()["documents"]]
    facts = [f for d in docs for f in d["facts"]]
    long_n = sum(d["long_tokens"] for d in docs)
    words = sum(d["words"] for d in docs)
    by_kind: dict[str, list[bool]] = {}
    for f in facts:
        by_kind.setdefault(f["kind"], []).append(f["passed"])
    return {
        "documents": docs,
        "summary": {
            "documents": len(docs),
            "facts": len(facts),
            "facts_passed": sum(f["passed"] for f in facts),
            "fact_recall": sum(f["passed"] for f in facts) / len(facts),
            "by_kind": {k: f"{sum(v)}/{len(v)}" for k, v in sorted(by_kind.items())},
            "long_token_rate": long_n / words if words else 0.0,
            "empty_tables": sum(d["empty_tables"] for d in docs),
            "tables": sum(d["tables"] for d in docs),
        },
    }
