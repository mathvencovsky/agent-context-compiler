"""PDF resource limits (audit A2, 2026-10-28): decompression bombs and page cap.

A 204 KB PDF whose content stream inflates to 200 MiB reached 438 MiB of peak
memory. Limits must truncate deterministically AND say so in ``warnings``.
"""

from __future__ import annotations

import tracemalloc
import zlib
from datetime import UTC, datetime

import pytest

from app.config import Settings
from app.extract.ir import SourceInfo
from app.extract.pdf import PDF_TRUNCATED_WARNING_CODE, extract_pdf

_TEXT = b"BT /F1 12 Tf 72 712 Td (hello limits) Tj ET\n"


def _source() -> SourceInfo:
    return SourceInfo(
        url="https://docs.test/a.pdf",
        final_url="https://docs.test/a.pdf",
        content_type="application/pdf",
        fetched_at=datetime.now(UTC),
        etag=None,
        last_modified=None,
        content_hash="0" * 64,
        render_mode="pdf",
    )


def _pdf(streams: list[tuple[bytes, bytes]]) -> bytes:
    """A minimal PDF with one page per ``(filter_names, encoded_stream)``."""
    n_pages = len(streams)
    font_id = 3 + 2 * n_pages
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(n_pages)).encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids ["
        + kids
        + b"] /Count "
        + str(n_pages).encode()
        + b" >>",
    ]
    for i, (filters, data) in enumerate(streams):
        objs.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents "
            + f"{4 + 2 * i} 0 R".encode()
            + b" /Resources << /Font << /F1 "
            + f"{font_id} 0 R".encode()
            + b" >> >> >>"
        )
        filt = b" /Filter " + filters if filters else b""
        objs.append(
            b"<< /Length "
            + str(len(data)).encode()
            + filt
            + b" >>\nstream\n"
            + data
            + b"\nendstream"
        )
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out = b"%PDF-1.4\n"
    offsets = []
    for i, obj in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    ).encode()
    return out


def _truncation_warnings(doc: object) -> list[str]:
    return [w.message for w in doc.warnings if w.code == PDF_TRUNCATED_WARNING_CODE]  # type: ignore[attr-defined]


_SMALL = Settings(pdf_max_stream_bytes=1 << 20, pdf_max_decoded_bytes=4 << 20)


def test_flate_bomb_is_truncated_with_a_warning_and_bounded_memory() -> None:
    content = _TEXT + b" " * (96 << 20)
    pdf = _pdf([(b"/FlateDecode", zlib.compress(content, 9))])
    tracemalloc.start()
    try:
        doc = extract_pdf(pdf, _source(), settings=_SMALL)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert "hello limits" in " ".join(b.text for b in doc.blocks)
    assert _truncation_warnings(doc)
    assert peak < 48 * 2**20


def test_stacked_flate_filters_are_capped_per_stage() -> None:
    inner = zlib.compress(_TEXT + b" " * (64 << 20), 9)
    pdf = _pdf([(b"[/FlateDecode /FlateDecode]", zlib.compress(inner, 9))])
    doc = extract_pdf(pdf, _source(), settings=_SMALL)
    assert _truncation_warnings(doc)


def test_runlength_bomb_is_truncated() -> None:
    # Each 2-byte run (129, ' ') decodes to 128 spaces.
    data = bytearray()
    data += bytes([len(_TEXT) - 1]) + _TEXT
    data += bytes([129, 0x20]) * (40_000)  # ~5 MiB decoded
    data += bytes([128])
    pdf = _pdf([(b"/RunLengthDecode", bytes(data))])
    doc = extract_pdf(pdf, _source(), settings=_SMALL)
    assert "hello limits" in " ".join(b.text for b in doc.blocks)
    assert _truncation_warnings(doc)


def test_page_limit_truncates_with_a_warning() -> None:
    pages = [
        (b"", f"BT /F1 12 Tf 72 712 Td (page {i} text) Tj ET\n".encode())
        for i in range(1, 4)
    ]
    doc = extract_pdf(_pdf(pages), _source(), settings=Settings(pdf_max_pages=2))
    assert {b.page for b in doc.blocks} == {1, 2}
    messages = _truncation_warnings(doc)
    assert any("1 more pages" in m for m in messages)


@pytest.mark.parametrize("filters", [b"/FlateDecode", b"/RunLengthDecode", b""])
def test_streams_within_budget_extract_with_no_truncation(filters: bytes) -> None:
    if filters == b"/FlateDecode":
        data = zlib.compress(_TEXT)
    elif filters == b"/RunLengthDecode":
        data = bytes([len(_TEXT) - 1]) + _TEXT + bytes([128])
    else:
        data = _TEXT
    doc = extract_pdf(_pdf([(filters, data)]), _source())
    assert "hello limits" in " ".join(b.text for b in doc.blocks)
    assert not _truncation_warnings(doc)


def test_pdfminer_decoding_is_untouched_outside_extract_pdf() -> None:
    import pdfminer.pdftypes as pdftypes

    blob = zlib.compress(b"x" * (2 << 20))
    assert pdftypes.zlib.decompress(blob) == b"x" * (2 << 20)
