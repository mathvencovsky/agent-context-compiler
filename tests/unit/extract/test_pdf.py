"""PDF -> IR extraction tests (M4 / FEAT-003).

These tests run on the reportlab-GENERATED fixtures under
``tests/fixtures/pdf/`` (see that directory's SOURCES.md). They are NOT marked
``browser`` and need NO network: ``extract_pdf`` is pure and deterministic. The
suite asserts the SPEC §4 PDF contract — relative-font-size headings, tables via
``extract_tables`` (with ``table_no_header`` when a table lacks a header),
repeated header/footer removal, 1-based non-decreasing pages, ``render_mode``
and the id prefixes — plus the ``extraction_empty`` (HTTP 422) case for the
text-less PDF, and a syrupy snapshot of one document's serialized IR for
deterministic regression.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest
from syrupy.assertion import SnapshotAssertion

from app.extract.ir import Document, SourceInfo
from app.extract.pdf import extract_pdf
from app.fetch.errors import ExtractionEmpty

# Directory holding the generated PDF fixtures.
PDF_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "pdf"

# Fixed timestamp so the snapshot of the serialized IR never drifts.
_FIXED_FETCHED_AT = datetime(2026, 10, 11, 0, 0, 0, tzinfo=UTC)
_BASE_URL = "https://fixtures.nina.test/pdf"

# The repeated running header/footer the extractor must strip (SOURCES.md).
_HEADER_TEXT = "NINA Technical Reference"
_FOOTER_TEXT = "Page chrome, not content"


def _source_for(stem: str, body: bytes) -> SourceInfo:
    """Build a deterministic ``render_mode='pdf'`` SourceInfo for a fixture."""
    final_url = f"{_BASE_URL}/{stem}.pdf"
    return SourceInfo(
        url=final_url,
        final_url=final_url,
        content_type="application/pdf",
        fetched_at=_FIXED_FETCHED_AT,
        etag=None,
        last_modified=None,
        content_hash=hashlib.sha256(body).hexdigest(),
        render_mode="pdf",
    )


def _extract(stem: str) -> Document:
    body = (PDF_DIR / f"{stem}.pdf").read_bytes()
    return extract_pdf(body, _source_for(stem, body))


_TECHNICAL = ("api_reference", "config_guide", "release_notes")
_TITLES = {
    "api_reference": "NINA API Reference",
    "config_guide": "NINA Configuration Guide",
    "release_notes": "NINA Release Notes",
}


@pytest.mark.parametrize("stem", _TECHNICAL)
def test_title_becomes_level_one_heading(stem: str) -> None:
    doc = _extract(stem)
    headings = [b for b in doc.blocks if b.type == "heading"]
    assert headings, f"{stem}: no heading blocks extracted"

    level_one = [b for b in headings if b.level == 1]
    assert len(level_one) == 1, f"{stem}: expected exactly one level-1 heading"
    assert level_one[0].text == _TITLES[stem]

    # Section headings are inferred as level 2 (the second-largest font).
    assert any(b.level == 2 for b in headings), f"{stem}: no level-2 headings"
    # Levels are coherent: only 1 and 2 are used, never deeper or 0/None mix.
    assert {b.level for b in headings} <= {1, 2}


def test_headerful_table_has_headers_and_rows() -> None:
    doc = _extract("api_reference")
    tables = [b for b in doc.blocks if b.type == "table"]
    assert tables, "api_reference: no table block extracted"

    table = tables[0]
    headers = table.data["headers"]
    rows = table.data["rows"]
    assert headers == ["Code", "Status", "Meaning"]
    assert ["extraction_empty", "422", "No extractable text was found"] in rows
    # A table with a detectable header emits NO table_no_header warning.
    assert not any(w.code == "table_no_header" for w in doc.warnings)


def test_headerless_table_warns() -> None:
    doc = _extract("release_notes")
    tables = [b for b in doc.blocks if b.type == "table"]
    assert tables, "release_notes: no table block extracted"

    headerless = [b for b in tables if not b.data["headers"]]
    assert headerless, "release_notes: expected a header-less table"

    warnings = [w for w in doc.warnings if w.code == "table_no_header"]
    assert warnings, "release_notes: table_no_header warning missing"
    # The warning is paired to its block (html.py finalization pattern).
    assert warnings[0].block_id == headerless[0].block_id


@pytest.mark.parametrize("stem", _TECHNICAL)
def test_repeated_header_and_footer_removed(stem: str) -> None:
    doc = _extract(stem)
    for block in doc.blocks:
        assert _HEADER_TEXT not in block.text, (
            f"{stem}: repeated header leaked into a {block.type} block"
        )
        assert _FOOTER_TEXT not in block.text, (
            f"{stem}: repeated footer leaked into a {block.type} block"
        )


@pytest.mark.parametrize("stem", _TECHNICAL)
def test_every_block_has_monotonic_one_based_page(stem: str) -> None:
    doc = _extract(stem)
    assert doc.blocks, f"{stem}: no blocks extracted"
    pages = [block.page for block in doc.blocks]
    assert all(p is not None and p >= 1 for p in pages), f"{stem}: a page is < 1"
    assert pages == sorted(pages), f"{stem}: pages are not non-decreasing"


@pytest.mark.parametrize("stem", _TECHNICAL)
def test_provenance(stem: str) -> None:
    doc = _extract(stem)
    assert doc.source.render_mode == "pdf"
    assert doc.document_id.startswith("doc_")
    assert doc.version_id.startswith("ver_")
    for block in doc.blocks:
        assert block.block_id.startswith("b_")


def test_short_document_warns_low_text_content() -> None:
    doc = _extract("release_notes")
    assert any(w.code == "low_text_content" for w in doc.warnings)


def test_textless_pdf_raises_extraction_empty() -> None:
    body = (PDF_DIR / "textless.pdf").read_bytes()
    source = _source_for("textless", body)
    with pytest.raises(ExtractionEmpty) as exc_info:
        extract_pdf(body, source)
    assert exc_info.value.http_status == 422
    assert exc_info.value.code == "extraction_empty"
    assert "OCR" in exc_info.value.message


def test_serialized_ir_snapshot(snapshot: SnapshotAssertion) -> None:
    """Deterministic regression snapshot of one technical PDF's IR."""
    doc = _extract("api_reference")
    assert doc.model_dump(mode="json") == snapshot
