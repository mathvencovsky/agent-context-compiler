"""Document metadata and JSON-LD extraction (SPEC §4).

``extract_metadata`` reads the document-level :class:`~app.extract.ir.Metadata`
from raw HTML: the title, language and publish/modify timestamps, plus the raw
JSON-LD blocks. The JSON-LD is passed through ``extruct`` and stored UNCHANGED
(SPEC §4: "blocos JSON-LD encontrados, sem alteração") — no normalization, no
flattening, no reordering beyond the document order extruct already yields.

Field resolution uses layered fallbacks, each preferring the most explicit
signal first:

- ``title``   : ``<title>`` → ``og:title`` → ``twitter:title`` → first JSON-LD
  ``headline``/``name``.
- ``lang``    : ``<html lang>`` → ``og:locale`` → ``<meta http-equiv>`` content
  language.
- ``published_at`` : ``article:published_time`` → ``og:...published_time`` →
  JSON-LD ``datePublished``.
- ``modified_at``  : ``article:modified_time`` → ``og:...modified_time`` →
  JSON-LD ``dateModified``.

Everything here is deterministic and pure: parsing is done with ``lxml.html``,
JSON-LD iteration preserves document order, and no network or LLM is involved
(CLAUDE.md). The function is intentionally tolerant — a page missing every
signal yields a :class:`Metadata` whose scalar fields are ``None`` and whose
``jsonld`` list is empty, never an exception.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import extruct
import lxml.html
from lxml import etree

from app.extract.ir import Metadata
from app.fetch.detect import detect_charset, parse_content_type

if TYPE_CHECKING:
    from lxml.html import HtmlElement

    from app.extract.ir import SourceInfo


def _ensure_text(raw: bytes | str, source: SourceInfo) -> str:
    """Decode ``raw`` to text, honoring the source Content-Type charset.

    ``bytes`` are decoded with the charset resolved by
    :func:`app.fetch.detect.detect_charset` (Content-Type charset, then BOM,
    then utf-8), replacing undecodable bytes so extraction never fails on a
    malformed page. ``str`` input is returned unchanged.
    """
    if isinstance(raw, str):
        return raw
    _, charset = parse_content_type(source.content_type)
    codec = detect_charset(content_type_charset=charset, body=raw)
    return raw.decode(codec, errors="replace")


def _first_meta(tree: HtmlElement, names: list[tuple[str, str]]) -> str | None:
    """Return the first non-empty ``<meta>`` content among ``(attr, value)``.

    ``names`` is tried in order; for each pair the first ``<meta>`` whose
    ``attr`` equals ``value`` (case-insensitive) and that carries a non-empty
    ``content`` wins. Document order is preserved within each lookup.
    """
    for attr, value in names:
        for meta in tree.iter("meta"):
            if (meta.get(attr) or "").strip().lower() == value.lower():
                content = (meta.get("content") or "").strip()
                if content:
                    return content
    return None


def _extract_title(tree: HtmlElement, jsonld: list[dict[str, object]]) -> str | None:
    """Resolve the document title with the documented fallback chain."""
    for el in tree.iter("title"):
        text = (el.text_content() or "").strip()
        if text:
            return text
    meta_title = _first_meta(
        tree,
        [("property", "og:title"), ("name", "og:title"), ("name", "twitter:title")],
    )
    if meta_title:
        return meta_title
    for block in jsonld:
        for key in ("headline", "name"):
            value = block.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _extract_lang(tree: HtmlElement) -> str | None:
    """Resolve the document language: ``<html lang>`` then meta fallbacks."""
    for html_el in tree.iter("html"):
        lang = (html_el.get("lang") or "").strip()
        if lang:
            return lang
    locale = _first_meta(tree, [("property", "og:locale"), ("name", "og:locale")])
    if locale:
        return locale
    for meta in tree.iter("meta"):
        if (meta.get("http-equiv") or "").strip().lower() == "content-language":
            content = (meta.get("content") or "").strip()
            if content:
                return content
    return None


def _jsonld_date(jsonld: list[dict[str, object]], key: str) -> str | None:
    """Return the first string ``key`` found across JSON-LD blocks, in order."""
    for block in jsonld:
        value = block.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _extract_jsonld(text: str, base_url: str) -> list[dict[str, object]]:
    """Return the raw JSON-LD blocks via ``extruct``, unchanged and in order.

    Only the ``json-ld`` syntax is requested. ``extruct`` yields the blocks as
    plain dicts; they are returned as-is (SPEC §4). Non-dict entries (JSON-LD
    permits top-level arrays/scalars) are skipped so the typed ``list[dict]``
    contract holds, which never drops the common object-shaped blocks pages use.
    """
    if not text.strip():
        return []
    extracted = extruct.extract(text, base_url=base_url, syntaxes=["json-ld"])
    blocks = extracted.get("json-ld", [])
    return [block for block in blocks if isinstance(block, dict)]


def extract_metadata(raw: bytes | str, source: SourceInfo) -> Metadata:
    """Build :class:`Metadata` from raw HTML (SPEC §4).

    ``raw`` is the fetched body (bytes) or already-decoded text; ``source``
    provides the Content-Type charset used to decode bytes and the ``final_url``
    used as the base for JSON-LD relative references. The JSON-LD blocks are
    stored exactly as ``extruct`` returns them.
    """
    text = _ensure_text(raw, source)
    try:
        tree = lxml.html.fromstring(text) if text.strip() else None
    except etree.ParserError:  # "Document is empty": only comments or PIs
        tree = None
    if tree is None:
        return Metadata(
            title=None, lang=None, published_at=None, modified_at=None, jsonld=[]
        )
    jsonld = _extract_jsonld(text, source.final_url)

    return Metadata(
        title=_extract_title(tree, jsonld),
        lang=_extract_lang(tree),
        published_at=_first_meta(
            tree,
            [
                ("property", "article:published_time"),
                ("name", "article:published_time"),
                ("property", "og:article:published_time"),
            ],
        )
        or _jsonld_date(jsonld, "datePublished"),
        modified_at=_first_meta(
            tree,
            [
                ("property", "article:modified_time"),
                ("name", "article:modified_time"),
                ("property", "og:article:modified_time"),
                ("property", "og:updated_time"),
            ],
        )
        or _jsonld_date(jsonld, "dateModified"),
        jsonld=jsonld,
    )
