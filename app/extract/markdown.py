"""Markdown and plain-text extraction to the IR (SPEC §4, M10b).

Documentation sites increasingly publish the page's author source as Markdown
(llms.txt v2 ``rel=alternate type=text/markdown``, Mintlify ``.md``, GitBook,
GitHub raw files), and some references are plain text (RFC ``.txt``, Sphinx
``_sources/*.rst.txt``). This module turns both into the SAME IR the HTML and
PDF walkers produce, so to_markdown, the chunker and assemble work unchanged.

* :func:`extract_markdown` parses CommonMark + GFM tables/strikethrough with
  ``markdown-it-py`` (MIT; DECISIONS.md 2026-10-29) and maps its token stream to
  blocks: headings (ATX and setext), paragraphs, fenced/indented code with the
  fence language, nested lists (two-space indentation per level, as SPEC §4),
  GFM tables, blockquotes, and GFM alerts (``> [!NOTE]``) as ``admonition``.
  Raw HTML blocks are reduced to their text (scripts, styles and comments
  dropped). A YAML/TOML front matter is removed; its ``title`` becomes the
  document title.
* :func:`extract_plain_text` splits on blank lines into paragraphs, keeps
  blocks that look preformatted (ASCII tables and diagrams, aligned columns) as
  ``code`` byte for byte, drops RFC page headers/footers and recognizes
  RFC-style numbered section titles (``3.2.1.  Title``) as headings, so
  heading-aware chunking works on them.

Both are deterministic, perform no network access and run the same invisible
Unicode stripping as the HTML walker before ids are assigned. The text is page
content (``trust: untrusted_source``): nothing here interprets it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urljoin

import lxml.html
from lxml import etree
from markdown_it import MarkdownIt
from markdown_it.token import Token

from app.extract.invisible import (
    invisible_text_warning,
    strip_invisible,
    strip_invisible_blocks,
)
from app.extract.ir import (
    Block,
    BlockType,
    Document,
    Link,
    Metadata,
    SourceInfo,
    Warning,
    assign_block_ids,
    make_document_id,
    make_version_id,
    normalize_text,
)

#: Media types parsed as Markdown, and the plain-text one (SPEC §4).
MARKDOWN_CONTENT_TYPES = frozenset({"text/markdown", "text/x-markdown"})
PLAIN_TEXT_CONTENT_TYPE = "text/plain"

#: ``text/plain`` URLs that are Markdown by convention (raw.githubusercontent.com
#: serves ``README.md`` as text/plain; llms.txt is Markdown by its spec).
_MARKDOWN_PATH = re.compile(
    r"(?:\.(?:md|markdown|mdx)|/llms(?:-full)?\.txt)$", re.IGNORECASE
)

#: GFM alert kinds -> the five SPEC §4 admonition kinds (the inverse of the
#: mapping to_markdown uses, so a round trip keeps the kind).
_ALERT_KINDS = {
    "note": "note",
    "tip": "tip",
    "important": "info",
    "warning": "warning",
    "caution": "danger",
}
_ALERT = re.compile(r"^\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]\s*", re.IGNORECASE)

#: MDX/Mintlify-style callout components written as raw HTML in the source.
_COMPONENT_KINDS = {
    "note": "note",
    "info": "info",
    "tip": "tip",
    "check": "tip",
    "warning": "warning",
    "caution": "warning",
    "danger": "danger",
    "callout": "note",
}

#: A raw inline ``<a href=...>`` (its text and target still count as a link).
_ANCHOR_OPEN = re.compile(
    r"""<a\s[^>]*?\bhref\s*=\s*(?:"(?P<q1>[^"]*)"|'(?P<q2>[^']*)'|(?P<bare>[^\s>]+))""",
    re.IGNORECASE,
)
#: A paragraph that is one MDX callout component: ``<Note>...</Note>``.
_COMPONENT_PARAGRAPH = re.compile(r"^<(?P<tag>[A-Za-z]+)>.*</(?P=tag)>$", re.DOTALL)

_LANGUAGE = re.compile(r"^[a-z0-9_+#.-]{1,32}$")
_DROPPED_HTML = ("script", "style", "template", "noscript")

_FRONT_MATTER_MAX_LINES = 200
_YAML_TITLE = re.compile(r"""^title\s*:\s*(?P<v>.+?)\s*$""", re.IGNORECASE)
_TOML_TITLE = re.compile(r"""^title\s*=\s*(?P<v>.+?)\s*$""", re.IGNORECASE)


def is_markdown_url(url: str) -> bool:
    """True if a ``text/plain`` response at ``url`` is Markdown by convention."""
    path = url.split("?", 1)[0].split("#", 1)[0]
    return bool(_MARKDOWN_PATH.search(path))


#: Longest inline run (a paragraph, heading or table cell) given to the inline
#: parser. markdown-it's inline rules are quadratic on crafted runs such as
#: ``[a](`` or ``<a `` repeated (1.6 MB of ``[a](`` took 75 s); a longer run
#: keeps its text with the markup characters left in place.
_INLINE_MAX_CHARS = 20_000


def _parse(body: str) -> list[Token]:
    """Block-parse ``body`` (linear), then inline-parse each bounded run.

    The commonmark preset keeps maxNesting=20, which bounds the recursion on
    adversarial input; html=True so raw HTML arrives as html_* tokens that are
    reduced to text (never passed through).
    """
    block = MarkdownIt("commonmark").enable(["table", "strikethrough"])
    inline = MarkdownIt("commonmark").enable(["table", "strikethrough"])
    block.disable("inline")
    env: dict[str, object] = {}  # link reference definitions, shared
    tokens = block.parse(body, env)
    for tok in tokens:
        if tok.type != "inline":
            continue
        if len(tok.content) <= _INLINE_MAX_CHARS:
            parsed = inline.parseInline(tok.content, env)
            tok.children = parsed[0].children if parsed else []
        else:
            tok.children = [Token("text", "", 0, content=tok.content)]
    return tokens


# --------------------------------------------------------------------------- #
# Front matter
# --------------------------------------------------------------------------- #
def _strip_front_matter(text: str) -> tuple[str, str | None]:
    """Remove a leading YAML (``---``) or TOML (``+++``) front matter.

    Returns the remaining text and the front matter's ``title`` (unquoted), if
    any. A fence that never closes within the first lines is not front matter.
    """
    lines = text.split("\n")
    if not lines or lines[0].strip() not in ("---", "+++"):
        return text, None
    fence = lines[0].strip()
    closers = ("---", "...") if fence == "---" else ("+++",)
    pattern = _YAML_TITLE if fence == "---" else _TOML_TITLE
    for i in range(1, min(len(lines), _FRONT_MATTER_MAX_LINES)):
        if lines[i].strip() in closers:
            title: str | None = None
            for line in lines[1:i]:
                match = pattern.match(line)
                if match:
                    title = normalize_text(match.group("v").strip("\"'")) or None
                    break
            # Keep the line count so token line maps stay meaningful.
            return "\n" * (i + 1) + "\n".join(lines[i + 1 :]), title
    return text, None


# --------------------------------------------------------------------------- #
# Inline text
# --------------------------------------------------------------------------- #
def _inline(token: Token | None, base_url: str) -> tuple[str, list[Link]]:
    """Plain, normalized text of an ``inline`` token, and its links.

    Markup is dropped (emphasis, inline code backticks), images are skipped (as
    the HTML walker skips ``<img>``), raw inline HTML tags are removed and the
    text inside inline ``<script>``/``<style>`` is dropped.
    """
    if token is None or not token.children:
        return "", []
    parts: list[str] = []
    links: list[Link] = []
    open_links: list[tuple[str, int]] = []
    skip = 0
    for child in token.children:
        kind = child.type
        if kind == "html_inline":
            tag = child.content.lower()
            anchor = _ANCHOR_OPEN.match(child.content)
            if anchor is not None and not skip:
                href = anchor.group("q1") or anchor.group("q2") or anchor.group("bare")
                open_links.append((href or "", len(parts)))
            elif tag.startswith("</a") and open_links and not skip:
                href, start = open_links.pop()
                if href:
                    text = normalize_text("".join(parts[start:]))
                    links.append(Link(text=text, href=urljoin(base_url, href)))
            elif any(tag.startswith(f"<{name}") for name in _DROPPED_HTML):
                skip += 1
            elif any(tag.startswith(f"</{name}") for name in _DROPPED_HTML):
                skip = max(0, skip - 1)
            continue
        if skip:
            continue
        if kind in ("text", "code_inline"):
            parts.append(child.content)
        elif kind in ("softbreak", "hardbreak"):
            parts.append(" ")
        elif kind == "link_open":
            open_links.append((str(child.attrGet("href") or ""), len(parts)))
        elif kind == "link_close" and open_links:
            href, start = open_links.pop()
            if href:
                text = normalize_text("".join(parts[start:]))
                links.append(Link(text=text, href=urljoin(base_url, href)))
    return normalize_text("".join(parts)), links


# --------------------------------------------------------------------------- #
# Token walker
# --------------------------------------------------------------------------- #
def _close_index(tokens: list[Token], start: int) -> int:
    """Index of the token closing the container opened at ``start``."""
    depth = 0
    for i in range(start, len(tokens)):
        depth += tokens[i].nesting
        if depth == 0:
            return i
    return len(tokens) - 1


def _language(info: str) -> str | None:
    """The language of a fence info string (``python title="x"`` -> python)."""
    word = info.strip().split(maxsplit=1)[0] if info.strip() else ""
    word = word.strip("{}").lstrip(".").lower()
    if word.startswith("language-"):
        word = word[len("language-") :]
    return word if _LANGUAGE.match(word) else None


@dataclass
class _Builder:
    """Accumulates blocks in document order with the SPEC §4 heading path."""

    base_url: str
    blocks: list[Block] = field(default_factory=list)
    stack: list[tuple[int, str]] = field(default_factory=list)
    first_h1: str | None = None

    def emit(
        self,
        block_type: BlockType,
        *,
        text: str,
        data: dict[str, object] | None = None,
        links: list[Link] | None = None,
        level: int | None = None,
    ) -> None:
        self.blocks.append(
            Block(
                block_id="",  # assigned by assign_block_ids
                type=block_type,
                order=len(self.blocks),
                level=level,
                heading_path=[title for _, title in self.stack],
                text=text,
                data=data or {},
                links=links or [],
                page=None,
            )
        )

    def heading(self, level: int, text: str, links: list[Link]) -> None:
        if not text:
            return
        while self.stack and self.stack[-1][0] >= level:
            self.stack.pop()
        self.emit("heading", text=text, links=links, level=level)
        self.stack.append((level, text))
        if level == 1 and self.first_h1 is None:
            self.first_h1 = text

    def code(self, code: str, language: str | None) -> None:
        if code.strip():
            self.emit("code", text=code, data={"language": language, "code": code})


class _MarkdownWalker:
    """Maps a markdown-it token stream to IR blocks."""

    def __init__(self, tokens: list[Token], builder: _Builder) -> None:
        self.tokens = tokens
        self.out = builder

    def walk(self, start: int, end: int) -> None:
        i = start
        while i < end:
            i = self._visit(i)

    def _visit(self, i: int) -> int:
        tok = self.tokens[i]
        kind = tok.type
        base = self.out.base_url
        if kind == "heading_open":
            text, links = _inline(self._next_inline(i), base)
            self.out.heading(int(tok.tag[1]), text, links)
            return _close_index(self.tokens, i) + 1
        if kind == "paragraph_open":
            inline = self._next_inline(i)
            text, links = _inline(inline, base)
            component = _COMPONENT_PARAGRAPH.match(inline.content if inline else "")
            kind_of = (
                _COMPONENT_KINDS.get(component.group("tag").lower())
                if component
                else None
            )
            if text and kind_of is not None:
                self.out.emit(
                    "admonition", text=text, data={"kind": kind_of}, links=links
                )
            elif text:
                self.out.emit("paragraph", text=text, links=links)
            return _close_index(self.tokens, i) + 1
        if kind in ("fence", "code_block"):
            language = _language(tok.info) if kind == "fence" else None
            self.out.code(tok.content, language)
            return i + 1
        if kind in ("bullet_list_open", "ordered_list_open"):
            return self._list(i)
        if kind == "blockquote_open":
            return self._blockquote(i)
        if kind == "table_open":
            return self._table(i)
        if kind == "html_block":
            self._html_block(tok.content)
            return i + 1
        if tok.nesting == 1:  # any other container: descend into it
            close = _close_index(self.tokens, i)
            self.walk(i + 1, close)
            return close + 1
        return i + 1  # hr, stray closers

    def _next_inline(self, i: int) -> Token | None:
        nxt = self.tokens[i + 1] if i + 1 < len(self.tokens) else None
        return nxt if nxt is not None and nxt.type == "inline" else None

    # -- lists --------------------------------------------------------------
    def _list(self, i: int) -> int:
        close = _close_index(self.tokens, i)
        items: list[str] = []
        links: list[Link] = []
        deferred: list[int] = []
        self._collect_items(i, close, 0, items, links, deferred)
        if items:
            self.out.emit(
                "list",
                text=normalize_text(" ".join(items)),
                data={
                    "ordered": self.tokens[i].type == "ordered_list_open",
                    "items": items,
                },
                links=links,
            )
        # Code, tables and quotes inside items become their own blocks right
        # after the list (never duplicated inside the list text).
        for j in deferred:
            self._visit(j)
        return close + 1

    def _collect_items(
        self,
        start: int,
        close: int,
        depth: int,
        items: list[str],
        links: list[Link],
        deferred: list[int],
    ) -> None:
        j = start + 1
        while j < close:
            tok = self.tokens[j]
            if tok.type != "list_item_open":
                j += 1
                continue
            item_close = _close_index(self.tokens, j)
            texts: list[str] = []
            nested: list[tuple[int, int]] = []
            k = j + 1
            while k < item_close:
                inner = self.tokens[k]
                if inner.type == "paragraph_open":
                    text, found = _inline(self._next_inline(k), self.out.base_url)
                    if text:
                        texts.append(text)
                    links.extend(found)
                    k = _close_index(self.tokens, k) + 1
                elif inner.type in ("bullet_list_open", "ordered_list_open"):
                    nested_close = _close_index(self.tokens, k)
                    nested.append((k, nested_close))
                    k = nested_close + 1
                elif inner.nesting == 1:
                    deferred.append(k)
                    k = _close_index(self.tokens, k) + 1
                else:
                    if inner.type in ("fence", "code_block", "html_block"):
                        deferred.append(k)
                    k += 1
            items.append("  " * depth + " ".join(texts))
            for n_start, n_close in nested:
                self._collect_items(n_start, n_close, depth + 1, items, links, deferred)
            j = item_close + 1

    # -- blockquotes and alerts ----------------------------------------------
    def _blockquote(self, i: int) -> int:
        close = _close_index(self.tokens, i)
        texts: list[str] = []
        links: list[Link] = []
        deferred: list[int] = []
        k = i + 1
        while k < close:
            tok = self.tokens[k]
            if tok.type == "inline":
                text, found = _inline(tok, self.out.base_url)
                if text:
                    texts.append(text)
                links.extend(found)
                k += 1
            elif tok.type in ("fence", "code_block", "html_block", "table_open"):
                deferred.append(k)
                k = _close_index(self.tokens, k) + 1 if tok.nesting == 1 else k + 1
            else:
                k += 1
        text = normalize_text(" ".join(texts))
        alert = _ALERT.match(text)
        if alert:
            kind = _ALERT_KINDS[alert.group(1).lower()]
            self.out.emit(
                "admonition",
                text=text[alert.end() :],
                data={"kind": kind},
                links=links,
            )
        elif text:
            self.out.emit("blockquote", text=text, links=links)
        for j in deferred:
            self._visit(j)
        return close + 1

    # -- tables ---------------------------------------------------------------
    def _table(self, i: int) -> int:
        close = _close_index(self.tokens, i)
        headers: list[str] = []
        rows: list[list[str]] = []
        links: list[Link] = []
        row: list[str] | None = None
        in_head = False
        for k in range(i + 1, close):
            tok = self.tokens[k]
            if tok.type == "thead_open":
                in_head = True
            elif tok.type == "thead_close":
                in_head = False
            elif tok.type == "tr_open":
                row = []
            elif tok.type == "tr_close" and row is not None:
                if in_head:
                    headers = row
                elif any(row):
                    rows.append(row)
                row = None
            elif tok.type == "inline" and row is not None:
                text, found = _inline(tok, self.out.base_url)
                row.append(text)
                links.extend(found)
        if not any(headers):
            headers = []
        if headers or rows:
            cells = [*headers, *(c for r in rows for c in r)]
            self.out.emit(
                "table",
                text=normalize_text(" ".join(cells)),
                data={"caption": None, "headers": headers, "rows": rows, "notes": []},
                links=links,
            )
        return close + 1

    # -- raw HTML -------------------------------------------------------------
    def _html_block(self, content: str) -> None:
        if not content.strip():
            return
        try:
            root = lxml.html.fragment_fromstring(content, create_parent="div")
        except (etree.ParserError, ValueError):
            return
        for el in list(root.iter(*_DROPPED_HTML)):
            el.drop_tree()
        text = normalize_text(root.text_content() or "")
        if not text:
            return
        links = [
            Link(
                text=normalize_text(a.text_content() or ""),
                href=urljoin(self.out.base_url, str(a.get("href"))),
            )
            for a in root.iter("a")
            if a.get("href")
        ]
        children = [c for c in root if isinstance(c.tag, str)]
        tag = children[0].tag.lower() if len(children) == 1 else ""
        kind = _COMPONENT_KINDS.get(tag)
        if kind is not None:
            self.out.emit("admonition", text=text, data={"kind": kind}, links=links)
        else:
            self.out.emit("paragraph", text=text, links=links)


# --------------------------------------------------------------------------- #
# Plain text
# --------------------------------------------------------------------------- #
#: An RFC page footer/header line ("Fielding, et al.  Standards Track  [Page 5]",
#: "RFC 9110            HTTP Semantics            June 2022").
_PAGE_FOOTER = re.compile(r"\[Page \d+\]\s*$")
_PAGE_HEADER = re.compile(r"^RFC \d+\s{2,}.*\s{2,}\S.*$")
#: A numbered section title at column 0 ("1.  Introduction", "3.2.1 Foo",
#: "Appendix A.  Collected ABNF"). The number needs at least one dot, so a
#: lone "200 OK" or "2026 release" line stays a paragraph.
_SECTION_TITLE = re.compile(
    r"^(?P<num>(?:\d+\.(?:\d+\.?)*)|(?:Appendix [A-Z](?:\.\d+)*\.?))\s+\S"
)
_SECTION_MAX_CHARS = 100
#: Lines longer than this are never page furniture or a figure row (and the
#: backtracking patterns below only ever see short lines).
_FURNITURE_MAX_CHARS = 200
_TITLE_MAX_CHARS = 120
#: Preformatted signals: box/diagram characters or column alignment.
_ART = re.compile(r"[+|]-{2,}|-{2,}[+|]|\|\s*\S.*\s\|")
_ALIGNED = re.compile(r"\S\s{3,}\S")


def _is_preformatted(lines: list[str]) -> bool:
    """True when a text block reads as a diagram, table or aligned listing."""
    signals = sum(
        1
        for line in lines
        if len(line) <= _FURNITURE_MAX_CHARS
        and (_ART.search(line) or _ALIGNED.search(line))
    )
    return signals >= 2 and signals * 2 >= len(lines)


def _section_level(line: str) -> int | None:
    """Heading level of an RFC-style numbered title line, else None."""
    if line != line.lstrip() or len(line) > _SECTION_MAX_CHARS:
        return None
    match = _SECTION_TITLE.match(line)
    if match is None or line.rstrip().endswith((".", ",", ";", ":")):
        return None
    num = match.group("num").rstrip(".")
    if num.startswith("Appendix"):
        return 1 + num.count(".")
    return min(1 + num.count("."), 6)


def _plain_paragraphs(text: str) -> list[list[str]]:
    """Blank-line separated blocks, without RFC page breaks/headers/footers."""
    blocks: list[list[str]] = []
    current: list[str] = []
    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.rstrip()
        short = len(line) <= _FURNITURE_MAX_CHARS
        if "\f" in line or (
            short and (_PAGE_FOOTER.search(line) or _PAGE_HEADER.match(line))
        ):
            continue  # page furniture never splits or joins a paragraph
        if not line.strip():
            if current:
                blocks.append(current)
                current = []
            continue
        current.append(line)
    if current:
        blocks.append(current)
    return blocks


def _walk_plain(text: str, builder: _Builder) -> None:
    for lines in _plain_paragraphs(text):
        level = _section_level(lines[0]) if len(lines) == 1 else None
        if level is not None:
            builder.heading(level, normalize_text(lines[0]), [])
        elif _is_preformatted(lines):
            indent = min(len(line) - len(line.lstrip()) for line in lines)
            builder.code("\n".join(line[indent:] for line in lines) + "\n", None)
        else:
            builder.emit("paragraph", text=normalize_text(" ".join(lines)))


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def _finish(builder: _Builder, source: SourceInfo, title: str | None) -> Document:
    removed = strip_invisible_blocks(builder.blocks)
    assign_block_ids(builder.blocks)
    warnings: list[Warning] = []
    if title is not None:
        clean, n = strip_invisible(title)
        if n:
            title = normalize_text(clean) or None
            removed += n
    if removed:
        warnings.append(invisible_text_warning(removed))
    return Document(
        document_id=make_document_id(source.url),
        version_id=make_version_id(make_document_id(source.url), source.content_hash),
        source=source,
        metadata=Metadata(
            title=title,
            lang=None,
            published_at=None,
            modified_at=None,
            jsonld=[],
        ),
        blocks=builder.blocks,
        warnings=warnings,
    )


def extract_markdown(text: str, source: SourceInfo) -> Document:
    """Extract a :class:`Document` from Markdown text (SPEC §4).

    Links resolve against ``source.final_url``; the title is the front matter
    ``title`` or else the first level-1 heading.
    """
    body, front_title = _strip_front_matter(text.replace("\r\n", "\n"))
    tokens = _parse(body)
    builder = _Builder(base_url=source.final_url)
    _MarkdownWalker(tokens, builder).walk(0, len(tokens))
    return _finish(builder, source, front_title or builder.first_h1)


def extract_plain_text(text: str, source: SourceInfo) -> Document:
    """Extract a :class:`Document` from plain text (SPEC §4).

    Paragraphs are blank-line separated; preformatted blocks stay verbatim as
    ``code``; RFC-style numbered titles become headings. The title is the first
    short paragraph before any heading (an RFC's centered title), else the
    first heading.
    """
    builder = _Builder(base_url=source.final_url)
    _walk_plain(text, builder)
    title: str | None = None
    for block in builder.blocks:
        if block.type == "heading":
            title = title or block.text
            break
        if block.type == "paragraph" and len(block.text) <= _TITLE_MAX_CHARS:
            title = block.text
            break
    return _finish(builder, source, title)
