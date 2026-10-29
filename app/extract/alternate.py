"""Markdown alternate discovery and the agreement check (SPEC §3.5, M10b).

A documentation page can declare the Markdown version of itself (llms.txt v2):
an HTML ``<link rel="alternate" type="text/markdown" href="...">`` or an HTTP
``Link: <...>; rel="alternate"; type="text/markdown"`` header. This module only
READS those declarations and compares texts; fetching the alternate (guard,
robots, size caps) happens in the resolve path (``app/api/alternate.py``).

* :func:`find_markdown_alternate` returns the first declared alternate on the
  SAME origin (scheme, host and port) as the page, or ``None``. A cross-origin
  alternate is never followed: another host could serve different content
  under the page's name.
* :func:`text_agreement` measures how much of the alternate's text a human
  reader of the HTML page also sees: the share of the alternate's word
  trigrams found in the page's visible text. Sites can show agents content
  that people never see (Mintlify ``<Visibility for="agents">``), which is an
  injection channel; a low agreement makes resolve keep the HTML extraction.

Pure functions: no network, deterministic (CLAUDE.md).
"""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlsplit

import lxml.html
from lxml import etree

MARKDOWN_MEDIA_TYPE = "text/markdown"

_TOKEN = re.compile(r"[a-z0-9]+")
_DEFAULT_PORTS = {"http": 80, "https": 443}
_DROPPED = ("script", "style", "template", "noscript")
#: One ``<url>; param; param`` entry of a Link header (RFC 8288); entries are
#: separated by commas that precede the next ``<``.
_LINK_SPLIT = re.compile(r",\s*(?=<)")
_LINK_ENTRY = re.compile(r"^\s*<(?P<url>[^>]*)>(?P<params>.*)$", re.DOTALL)
_PARAM = re.compile(r";\s*(?P<name>[a-zA-Z*]+)\s*=\s*(?P<value>\"[^\"]*\"|[^;]*)")


def _origin(url: str) -> tuple[str, str, int] | None:
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in _DEFAULT_PORTS or not parts.hostname:
        return None
    try:
        port = parts.port or _DEFAULT_PORTS[scheme]
    except ValueError:
        return None
    return scheme, parts.hostname.lower(), port


def same_origin(a: str, b: str) -> bool:
    """True when ``a`` and ``b`` share scheme, host and port (http/https only)."""
    origin = _origin(a)
    return origin is not None and origin == _origin(b)


def _is_markdown_alternate(rel: str, media_type: str) -> bool:
    rels = {r.lower() for r in rel.split()}
    media = media_type.split(";", 1)[0].strip().lower()
    return "alternate" in rels and media == MARKDOWN_MEDIA_TYPE


def _from_link_header(link_header: str | None) -> list[str]:
    if not link_header:
        return []
    found: list[str] = []
    for part in _LINK_SPLIT.split(link_header):
        entry = _LINK_ENTRY.match(part)
        if entry is None:
            continue
        params = {
            m.group("name").lower(): m.group("value").strip().strip('"')
            for m in _PARAM.finditer(entry.group("params"))
        }
        if _is_markdown_alternate(params.get("rel", ""), params.get("type", "")):
            found.append(entry.group("url").strip())
    return found


def _from_html(html_text: str) -> list[str]:
    if not html_text.strip():
        return []
    try:
        tree = lxml.html.fromstring(html_text)
    except (etree.ParserError, ValueError):
        return []
    return [
        str(link.get("href")).strip()
        for link in tree.iter("link")
        if link.get("href")
        and _is_markdown_alternate(link.get("rel") or "", link.get("type") or "")
    ]


def find_markdown_alternate(
    html_text: str, page_url: str, link_header: str | None = None
) -> str | None:
    """The page's declared same-origin Markdown alternate, as an absolute URL.

    The ``Link`` header is checked before the HTML ``<link>`` elements; the
    first declaration decides (a cross-origin first declaration means no
    alternate, rather than hunting for another one). Relative hrefs resolve
    against ``page_url`` (the page's final URL). ``None`` when nothing usable is
    declared, or when the alternate is the page itself.
    """
    candidates = [*_from_link_header(link_header), *_from_html(html_text)]
    if not candidates:
        return None
    url = urljoin(page_url, candidates[0]).split("#", 1)[0]
    if not same_origin(url, page_url) or url == page_url.split("#", 1)[0]:
        return None
    return url


def visible_text(html_text: str) -> str:
    """The text a reader of the HTML page can see (scripts and styles removed)."""
    if not html_text.strip():
        return ""
    try:
        tree = lxml.html.fromstring(html_text)
    except (etree.ParserError, ValueError):
        return ""
    for el in list(tree.iter(*_DROPPED)):
        el.drop_tree()
    return tree.text_content() or ""


def _trigrams(text: str) -> set[tuple[str, str, str]]:
    words = _TOKEN.findall(text.lower())
    return set(zip(words, words[1:], words[2:], strict=False))


def text_agreement(alternate_text: str, page_text: str) -> float:
    """Share of the alternate's word trigrams that also occur in the page text.

    1.0 means every three-word sequence of the alternate is visible on the
    HTML page; content shown only to agents lowers it. An alternate with fewer
    than three words scores 0.0 (nothing to verify).
    """
    alt = _trigrams(alternate_text)
    if not alt:
        return 0.0
    return len(alt & _trigrams(page_text)) / len(alt)
