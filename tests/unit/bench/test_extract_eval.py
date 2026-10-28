"""Tests for the M10b extraction-eval harness (bench/extract_eval/).

Offline and tiny: synthetic strings and hand-built documents. The real WCXB
and PDF data are never needed here.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from app.extract.ir import Block, Document, Metadata
from bench.extract_eval import download
from bench.extract_eval import metrics as m
from bench.extract_eval.fetch import (
    load_pdf_facts,
    load_wcxb_manifest,
    split_for,
)
from bench.extract_eval.run import compare
from bench.runners.ours import _source_for

# ------------------------------------------------------------------ text


def test_word_prf_matches_wcxb_definition() -> None:
    assert m.word_prf("", "") == (1.0, 1.0, 1.0)
    assert m.word_prf("x", "") == (0.0, 0.0, 0.0)
    assert m.word_prf("", "a b") == (0.0, 0.0, 0.0)
    p, r, f1 = m.word_prf("The cat, the DOG", "the cat sat")
    assert (p, r) == (2 / 4, 2 / 3)
    assert f1 == pytest.approx(2 * p * r / (p + r))


def test_leak_counts_only_words_absent_from_ground_truth() -> None:
    assert m.leak_rate("Copy page alpha beta", "alpha beta gamma") == 0.5
    # Repeating a ground-truth word (duplicated code) is not a leak.
    assert m.leak_rate("alpha alpha alpha", "alpha") == 0.0
    assert m.leak_rate("", "alpha") == 0.0


def test_snippet_rate() -> None:
    assert m.snippet_rate("anything", []) is None
    assert m.snippet_rate("On this page: Intro", ["on this page", "Edit"]) == 0.5


def test_glued_tokens_need_two_ground_truth_words() -> None:
    vocab = {"attribute", "data", "fine", "javascript", "is"}
    text = "AttributeData is fine. JavaScript IsOk FooBar"
    # JavaScript is itself a ground-truth word; IsOk has a 2-letter half;
    # FooBar's halves are not ground-truth words.
    assert m.glued_tokens(text, vocab) == ["AttributeData"]


def test_long_token_stats() -> None:
    assert m.long_token_stats("Withadvancesingenerative AI is here 2024") == (1, 4)
    assert m.long_token_stats("") == (0, 0)


# ------------------------------------------------------------------ code


def test_markdown_code_blocks() -> None:
    md = "intro\n```python\nimport os\n```\ntext\n~~~\na\n  b\n~~~\n```\nopen"
    assert m.markdown_code_blocks(md) == ["import os", "a\n  b", "open"]


def test_contained_skips_short_needles_and_collapses_space() -> None:
    hay = "x = compute(1,\n    2)\nok"
    assert m.contained(["x = compute(1, 2)", "ok"], hay) == (1, 1)
    assert m.contained(["y = compute(9, 9)"], hay) == (0, 1)


def test_line_number_leak() -> None:
    assert m.has_line_number_leak("1import os\n2print(1)\n3x = 1")
    assert m.has_line_number_leak(" 9# a\n10b = 2\n11c = 3")
    assert not m.has_line_number_leak("import os\nprint(1)")
    assert not m.has_line_number_leak("1import os\n2print(1)")
    assert not m.has_line_number_leak("1a\n5b\n9c")


def test_markdown_table_cells() -> None:
    md = "| Name | Type |\n|---|:---:|\n| `id` | int |\n\nno table | here\n"
    assert m.markdown_table_cells(md) == [["Name", "Type", "`id`", "int"]]


# ------------------------------------------------------------------ PDF facts


def _block(order: int, type_: str, text: str = "", page: int = 1, **data: Any) -> Block:
    return Block(
        block_id=f"b{order}",
        type=type_,  # type: ignore[arg-type]
        order=order,
        level=1 if type_ == "heading" else None,
        heading_path=[],
        text=text,
        data=data,
        links=[],
        page=page,
    )


def _doc(blocks: list[Block], title: str | None = None) -> Document:
    source, document_id, version_id = _source_for(
        "https://x.test/a.pdf", "application/pdf"
    )
    return Document(
        document_id=document_id,
        version_id=version_id,
        source=source,
        metadata=Metadata(
            title=title, lang=None, published_at=None, modified_at=None, jsonld=[]
        ),
        blocks=blocks,
        warnings=[],
    )


def test_evaluate_facts_each_kind() -> None:
    doc = _doc(
        [
            _block(0, "heading", "Deep Manual"),
            _block(1, "paragraph", "Alpha beta"),
            _block(2, "paragraph", "gamma delta."),
            _block(3, "heading", "1 Intro", page=2),
            _block(4, "table", "", page=2, headers=["Country"], rows=[["Vienna"]]),
            _block(5, "table", "", page=2, headers=[], rows=[]),
        ]
    )
    facts = {
        "title": "Deep Manual",
        "headings": ["1 Intro", "2 Missing"],
        "present": ["Alpha beta gamma delta.", "Alphabeta"],
        "order": [["beta", "delta"], ["delta", "beta"]],
        "tables": {"min": 1, "max": 1, "cells": ["Vienna", "Paris"]},
        "paragraphs": [
            {"page": 1, "min": 2, "max": 2},
            {"page": 2, "min": 1, "max": 3},
        ],
    }
    rows = {r["id"]: r["passed"] for r in m.evaluate_facts(doc, facts)}
    assert rows == {
        "title:0": True,
        "heading:0": True,
        "heading:1": False,
        "present:0": True,  # blocks joined by whitespace, collapsed
        "present:1": False,  # glued words never match
        "order:0": True,
        "order:1": False,
        "table_count:0": False,  # two tables, one of them empty
        "table_cell:0": True,
        "table_cell:1": False,
        "paragraphs:0": True,
        "paragraphs:1": False,
    }
    assert m.is_empty_table(doc.blocks[5]) and not m.is_empty_table(doc.blocks[4])
    assert m.paragraphs_per_page(doc) == 1.0  # 2 text blocks over 2 pages


def test_title_fact_accepts_metadata_title() -> None:
    doc = _doc([_block(0, "paragraph", "body")], title="The  RISC-V Manual")
    rows = m.evaluate_facts(doc, {"title": "The RISC-V Manual"})
    assert rows == [
        {"id": "title:0", "kind": "title", "passed": True, "fact": "The RISC-V Manual"}
    ]


# ------------------------------------------------------------------ statistics


def test_bootstrap_is_seeded_and_brackets_the_mean() -> None:
    values = [0.1, 0.5, 0.9, 0.4, 0.6]
    lo, hi = m.bootstrap_ci(values)
    assert lo <= m.mean(values) <= hi
    assert m.bootstrap_ci(values) == (lo, hi)
    assert m.paired_bootstrap_ci([0.5, 0.5], [0.6, 0.7])[0] == pytest.approx(0.15)
    with pytest.raises(ValueError):
        m.paired_bootstrap_ci([0.1], [0.1, 0.2])


# ------------------------------------------------------------------ manifests


def test_wcxb_manifest_split_is_the_fixed_hash_rule() -> None:
    manifest = load_wcxb_manifest()
    pages = manifest["pages"]
    assert manifest["source"]["license"] == "CC-BY-4.0"
    assert len({p["id"] for p in pages}) == len(pages) == manifest["counts"]["pages"]
    assert all(p["split"] == split_for(p["id"]) for p in pages)
    assert sum(p["split"] == "dev" for p in pages) == manifest["counts"]["dev"]
    hexes = [p["html_sha256"] for p in pages] + [p["gt_sha256"] for p in pages]
    assert all(re.fullmatch(r"[0-9a-f]{64}", h) for h in hexes)


def test_pdf_facts_file_is_well_formed() -> None:
    known = {"title", "headings", "present", "order", "tables", "paragraphs"}
    docs = load_pdf_facts()["documents"]
    assert len({d["id"] for d in docs}) == len(docs)
    for doc in docs:
        download.check_url(doc["download_url"])
        download.check_url(doc["license_url"])
        assert re.fullmatch(r"[0-9a-f]{64}", doc["sha256"])
        assert doc["license"] in {"CC-BY-4.0", "CC-BY-SA-4.0"}
        assert set(doc["facts"]) <= known and doc["facts"]


# ------------------------------------------------------------------ download


@pytest.mark.parametrize(
    "url",
    [
        "http://raw.githubusercontent.com/a/b/c",
        "https://example.com/x.pdf",
        "https://raw.githubusercontent.com:8443/a",
        "https://user:pw@github.com/a",
        "https://github.com.evil.test/a",
    ],
)
def test_check_url_refuses_outside_the_allowlist(url: str) -> None:
    with pytest.raises(download.DownloadError):
        download.check_url(url)


def test_ensure_file_verifies_checksum(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = b"pinned bytes"
    good = download.sha256_bytes(body)
    calls: list[str] = []

    def fake_fetch(url: str, **_: Any) -> bytes:
        calls.append(url)
        return body

    monkeypatch.setattr(download, "fetch_bytes", fake_fetch)
    dest = tmp_path / "d" / "f.bin"
    url = "https://raw.githubusercontent.com/o/r/c/f.bin"
    assert download.ensure_file(url, dest, good) is True
    assert dest.read_bytes() == body
    assert download.ensure_file(url, dest, good) is False  # verified, no request
    assert len(calls) == 1
    with pytest.raises(download.DownloadError, match="checksum mismatch"):
        download.ensure_file(url, tmp_path / "other.bin", "0" * 64)
    assert not (tmp_path / "other.bin").exists()


# ------------------------------------------------------------------ gates


def _run(f1s: list[float], facts: list[bool]) -> dict[str, Any]:
    zero = {"precision": 0.9, "recall": 0.9, "leak": 0.0, "without": None, "glued": 0}
    pages = [{"id": str(i), "nina": {**zero, "f1": f}} for i, f in enumerate(f1s)]
    nina = {
        "code_text_recall": None,
        "code_struct_recall": None,
        "table_cell_recall": None,
    }
    code = {"language_rate": None, "lineno_leak_blocks": 0}
    return {
        "html": {"dev": {"pages": pages, "summary": {"nina": nina, "nina_code": code}}},
        "pdf": {
            "documents": [
                {
                    "id": "d",
                    "facts": [
                        {"id": f"present:{i}", "passed": p, "fact": "s"}
                        for i, p in enumerate(facts)
                    ],
                }  # fmt: skip
            ],
            "summary": {
                "facts_passed": sum(facts),
                "facts": len(facts),
                "long_token_rate": 0.0,
                "empty_tables": 0,
            },  # fmt: skip
        },
    }


def test_compare_flags_f1_drop_and_fact_regression() -> None:
    base = _run([0.9] * 20, [True, False])
    assert compare(_run([0.9] * 20, [True, True]), base) == []
    failed = compare(_run([0.85] * 20, [False, True]), base)
    assert [f.split()[0] for f in failed] == ["H1", "P1"]
