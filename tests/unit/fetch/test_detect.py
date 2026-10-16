"""Content-Type parsing and charset detection (M1 subset)."""

from __future__ import annotations

import codecs

import pytest

from app.fetch import detect


@pytest.mark.parametrize(
    ("header", "media", "charset"),
    [
        ("text/html; charset=utf-8", "text/html", "utf-8"),
        ("text/HTML; Charset=UTF-8", "text/html", "utf-8"),
        ('application/json; charset="UTF-16"', "application/json", "utf-16"),
        ("text/plain", "text/plain", None),
        ("TEXT/PLAIN; boundary=x", "text/plain", None),
        (None, "", None),
        ("", "", None),
    ],
)
def test_parse_content_type(
    header: str | None, media: str, charset: str | None
) -> None:
    assert detect.parse_content_type(header) == (media, charset)


def test_charset_header_wins() -> None:
    assert (
        detect.detect_charset(content_type_charset="latin-1", body=b"abc") == "latin-1"
    )


def test_charset_fallback_default() -> None:
    assert detect.detect_charset(content_type_charset=None, body=b"abc") == "utf-8"


def test_charset_custom_default() -> None:
    got = detect.detect_charset(
        content_type_charset=None, body=b"abc", default="latin-1"
    )
    assert got == "latin-1"


@pytest.mark.parametrize(
    ("bom", "expected"),
    [
        (codecs.BOM_UTF8, "utf-8-sig"),
        (codecs.BOM_UTF16_LE, "utf-16"),
        (codecs.BOM_UTF16_BE, "utf-16"),
        (codecs.BOM_UTF32_LE, "utf-32"),
        (codecs.BOM_UTF32_BE, "utf-32"),
    ],
)
def test_bom_sniffing(bom: bytes, expected: str) -> None:
    assert (
        detect.detect_charset(content_type_charset=None, body=bom + b"data") == expected
    )


def test_meta_charset_is_used_after_header_and_bom() -> None:
    page = b'<html><head><meta charset="GBK"></head>'
    assert detect.detect_charset(content_type_charset=None, body=page) == "gbk"
    assert detect.detect_charset(content_type_charset="big5", body=page) == "big5"
    equiv = b'<meta http-equiv="Content-Type" content="text/html; charset=Shift_JIS">'
    assert detect.detect_charset(content_type_charset=None, body=equiv) == "shift_jis"


def test_meta_charset_unknown_late_or_utf16_falls_back_to_utf8() -> None:
    assert (
        detect.detect_charset(
            content_type_charset=None, body=b'<meta charset="x-nope">'
        )
        == "utf-8"
    )
    late = b" " * 1024 + b'<meta charset="gbk">'
    assert detect.detect_charset(content_type_charset=None, body=late) == "utf-8"
    assert (
        detect.detect_charset(
            content_type_charset=None, body=b'<meta charset="utf-16">'
        )
        == "utf-8"
    )
