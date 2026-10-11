"""Content-Type and charset detection (M1 subset of SPEC §3).

This module covers ONLY parsing the ``Content-Type`` header and resolving the
response charset. The render decision (SPEC §3.3) and any HTML/PDF parsing
belong to later milestones and are deliberately absent here. Functions are pure
and depend only on the standard library.
"""

from __future__ import annotations

import codecs

_DEFAULT_CHARSET = "utf-8"


def parse_content_type(header_value: str | None) -> tuple[str, str | None]:
    """Split a ``Content-Type`` header into ``(media_type, charset)``.

    The media type is lower-cased. A ``charset`` parameter is returned verbatim
    (quotes stripped, lower-cased) when present, else ``None``. A missing header
    yields ``("", None)``.
    """
    if not header_value:
        return "", None

    pieces = header_value.split(";")
    media_type = pieces[0].strip().lower()

    charset: str | None = None
    for param in pieces[1:]:
        name, _, value = param.partition("=")
        if name.strip().lower() == "charset":
            charset = value.strip().strip('"').strip("'").lower() or None
            break
    return media_type, charset


def detect_charset(
    *,
    content_type_charset: str | None,
    body: bytes,
    default: str = _DEFAULT_CHARSET,
) -> str:
    """Resolve the charset to decode ``body`` with.

    Precedence: an explicit charset from the Content-Type header wins; otherwise
    a byte-order mark at the start of the body is honored; otherwise ``default``
    ("utf-8"). This never decodes the body — it only names the codec to use.
    """
    if content_type_charset:
        return content_type_charset

    bom_charset = _sniff_bom(body)
    if bom_charset is not None:
        return bom_charset

    return default


def _sniff_bom(body: bytes) -> str | None:
    """Return the charset implied by a leading BOM, or ``None``."""
    if body.startswith(codecs.BOM_UTF8):
        return "utf-8-sig"
    # UTF-32 BOMs must be checked before UTF-16 (BOM_UTF32_LE starts with
    # BOM_UTF16_LE), otherwise a UTF-32 LE BOM is misread as UTF-16.
    if body.startswith(codecs.BOM_UTF32_LE) or body.startswith(codecs.BOM_UTF32_BE):
        return "utf-32"
    if body.startswith(codecs.BOM_UTF16_LE) or body.startswith(codecs.BOM_UTF16_BE):
        return "utf-16"
    return None
