"""Content-Type and charset detection (M1 subset of SPEC §3).

This module covers ONLY parsing the ``Content-Type`` header and resolving the
response charset. The render decision (SPEC §3.3) and any HTML/PDF parsing
belong to later milestones and are deliberately absent here. Functions are pure
and depend only on the standard library.
"""

from __future__ import annotations

import codecs
import re
from typing import Literal

from app.config import Settings, get_settings

_DEFAULT_CHARSET = "utf-8"

RenderDecision = Literal["auto", "never", "always"]


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
    a byte-order mark at the start of the body is honored; otherwise a
    ``<meta charset>`` / ``http-equiv`` declaration in the first 1024 bytes (as
    browsers prescan; GBK and Shift_JIS pages often declare only there);
    otherwise ``default`` ("utf-8"). This never decodes the body — it only
    names the codec to use.
    """
    if content_type_charset:
        return content_type_charset

    bom_charset = _sniff_bom(body)
    if bom_charset is not None:
        return bom_charset

    return _sniff_meta(body) or default


#: ``<meta charset=x>`` or ``<meta http-equiv=... content="...; charset=x">``.
_META_CHARSET = re.compile(
    rb"<meta[^>]*?charset\s*=\s*[\"']?\s*([a-z0-9_.:-]+)", re.IGNORECASE
)


def _sniff_meta(body: bytes) -> str | None:
    """The charset a ``<meta>`` declares in the first 1024 bytes, if Python knows it.

    A declared UTF-16/32 is read as UTF-8, as the HTML standard prescribes: the
    declaration was itself readable as ASCII, so the bytes are not UTF-16.
    """
    match = _META_CHARSET.search(body[:1024])
    if match is None:
        return None
    name = match.group(1).decode("ascii").lower()
    try:
        codec = codecs.lookup(name).name
    except LookupError:
        return None
    return "utf-8" if codec.startswith("utf-16") or codec.startswith("utf-32") else name


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


def should_render(
    *,
    main_text_len: int,
    html_text: str,
    render: RenderDecision = "auto",
    settings: Settings | None = None,
) -> bool:
    """Decide whether a browser render is needed (SPEC §3.3).

    ``never`` -> never render; ``always`` -> always render. In ``auto`` (the
    default) render ONLY IF both hold: the already-extracted main text is
    shorter than ``settings.render_min_text_chars`` AND the HTML carries SPA
    signals (see ``_has_spa_signals``). This function is pure: it reasons over
    ``main_text_len`` (measured by the caller from the HTTP HTML) and the raw
    ``html_text`` string plus config only — it performs NO network and NO
    browser work. ``settings`` is resolved via ``get_settings()`` when ``None``.
    """
    if render == "never":
        return False
    if render == "always":
        return True

    if settings is None:
        settings = get_settings()

    return main_text_len < settings.render_min_text_chars and _has_spa_signals(
        html_text
    )


# An element whose id is "root" or "app" (quotes optional) holding only
# whitespace before its closing tag — the classic empty SPA mount point.
_EMPTY_ROOT_CONTAINER = re.compile(
    r"<div\b[^>]*\bid\s*=\s*[\"']?(?:root|app)[\"']?[^>]*>\s*</div>",
    re.IGNORECASE,
)
# A <noscript> block that asks the visitor to enable/turn on JavaScript.
_NOSCRIPT_NEEDS_JS = re.compile(
    r"<noscript\b[^>]*>(?P<body>.*?)</noscript>",
    re.IGNORECASE | re.DOTALL,
)
_JS_PROMPT = re.compile(r"javascript", re.IGNORECASE)


def _has_spa_signals(html_text: str) -> bool:
    """Return True if ``html_text`` looks like an unrendered SPA (SPEC §3.3).

    Pure string inspection — no parsing of the DOM, no network. Signals:

    * an empty root/app mount container (``<div id="root"></div>`` /
      ``<div id="app"></div>`` with no textual content);
    * a client-side framework data island (``__NEXT_DATA__`` or
      ``window.__NUXT__``) with no rendered content around it;
    * a ``<noscript>`` block asking the visitor for JavaScript.
    """
    if _EMPTY_ROOT_CONTAINER.search(html_text):
        return True

    if "__NEXT_DATA__" in html_text or "window.__NUXT__" in html_text:
        return True

    for match in _NOSCRIPT_NEEDS_JS.finditer(html_text):
        if _JS_PROMPT.search(match.group("body")):
            return True

    return False
