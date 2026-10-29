"""HTML → IR extraction (SPEC §4).

``extract_html`` turns raw HTML into a :class:`~app.extract.ir.Document`. The
pipeline mirrors SPEC §4:

1. Decode the bytes (charset from the Content-Type, then BOM, then utf-8).
2. Parse once with ``lxml.html`` and locate the original ``<main>``/``<article>``
   subtree — kept for the mandatory readability fallback check.
3. Isolate the main content with ``readability-lxml`` (``readability.Document``).
4. CLEAN the chosen subtree: drop ``nav``/``footer``/``aside``, copy-code
   controls and permalink anchors, so none of that chrome leaks into any
   block's text.
5. Walk the cleaned subtree with a CUSTOM lxml walker that emits ordered blocks
   for the eight IR block types, each with the SPEC §4 ``data`` shape, absolute
   links (resolved against ``source.final_url``) and the ancestor
   ``heading_path``.
6. MANDATORY FALLBACK: if readability dropped a ``<table>`` or ``<pre>`` that
   existed in the original ``<main>``/``<article>``, re-walk that subtree
   directly and record a ``readability_fallback`` warning, guaranteeing 100% of
   main-content tables and code reach the IR.
7. Assign stable ``block_id``s (FEAT-001 ``assign_block_ids``) and assemble the
   Document.

Everything is deterministic and pure: no timestamps, no unordered-set iteration
in any compared output, no network and no LLM (CLAUDE.md). Code blocks are
preserved byte-for-byte; every other block carries whitespace-normalized,
markup-free text.
"""

from __future__ import annotations

import re
from copy import deepcopy
from typing import TYPE_CHECKING
from urllib.parse import urljoin

import lxml.html
from readability import Document as ReadabilityDocument

from app.extract.ir import (
    Block,
    Document,
    Link,
    Warning,
    assign_block_ids,
    make_document_id,
    make_version_id,
    normalize_text,
)
from app.extract.jsonld import extract_metadata
from app.fetch.detect import detect_charset, parse_content_type

if TYPE_CHECKING:
    from lxml.html import HtmlElement

    from app.extract.ir import SourceInfo

# Block-level containers the walker descends into looking for leaf blocks,
# rather than treating them as a single paragraph.
_CONTAINER_TAGS = frozenset(
    {
        "div",
        "section",
        "article",
        "main",
        "header",
        "body",
        "html",
        "figure",
        "details",
    }
)

# Tags removed wholesale during cleanup (SPEC §4): site chrome that must never
# appear in block text.
_CHROME_TAGS = frozenset({"nav", "footer", "aside", "script", "style", "noscript"})

# ARIA roles that mark site chrome rather than main content (removed in cleanup).
_CHROME_ROLES = frozenset({"navigation", "banner", "search", "complementary"})

# Class tokens that reliably mark site chrome living in generic <div>s — mostly
# Material for MkDocs and Sphinx theme widgets (navigation drawers, feedback
# widgets, tables of contents, source/footer bars). Matched as whole class
# tokens so a legitimate content word is never caught by substring (DECISIONS
# 2026-10-11, "M3 — limpeza de chrome por role/class"). Deterministic and
# conservative: these are structural chrome containers, not content.
_CHROME_CLASS_TOKENS = frozenset(
    {
        # Material for MkDocs
        "md-feedback",
        "md-sidebar",
        "md-footer",
        "md-source",
        "md-header",
        "md-nav",
        "md-skip",
        "md-tabs",
        # Sphinx / RTD-style themes
        "sphinxsidebar",
        "related",
        "breadcrumbs",
        "headerlink",
        "toc",
        "contents",
        "localtoc",
        "footer",
    }
)

# Text labels of chrome widgets that may still slip through (feedback prompts,
# skip links, "on this page" ToCs). Used only as a conservative COMPLEMENT to the
# role/class matching above, on short elements, so real prose is never removed.
_CHROME_TEXT_LABELS = (
    "was this page helpful",
    "was this helpful",
    "skip to content",
    "back to top",
)

# Admonition class/role → canonical kind (SPEC §4 five-value set).
_ADMONITION_KINDS = ("danger", "warning", "tip", "info", "note")
_ADMONITION_SYNONYMS = {
    "callout": "note",
    "alert": "warning",
    "caution": "warning",
    "error": "danger",
    "important": "warning",
    "hint": "tip",
    "seealso": "info",
}


# --------------------------------------------------------------------------- #
# decoding
# --------------------------------------------------------------------------- #
def _ensure_text(raw: bytes | str, source: SourceInfo) -> str:
    """Decode ``raw`` to text using the Content-Type charset (then BOM, utf-8)."""
    if isinstance(raw, str):
        return raw
    _, charset = parse_content_type(source.content_type)
    codec = detect_charset(content_type_charset=charset, body=raw)
    return raw.decode(codec, errors="replace")


# --------------------------------------------------------------------------- #
# cleanup
# --------------------------------------------------------------------------- #
def _is_copy_control(el: HtmlElement) -> bool:
    """True if ``el`` looks like a copy-code control (button/label/class)."""
    signals = " ".join(
        [
            (el.get("class") or ""),
            (el.get("aria-label") or ""),
            (el.get("title") or ""),
            (el.get("data-action") or ""),
        ]
    ).lower()
    text = normalize_text(el.text_content() or "").lower()
    needles = ("copy", "copiar", "clipboard")
    has_signal = any(n in signals for n in needles)
    if el.tag == "button" and (text in {"copy", "copiar"} or has_signal):
        return True
    return has_signal and el.tag in {"button", "span", "a", "div"} and len(text) <= 20


def _is_permalink(el: HtmlElement) -> bool:
    """True if ``el`` is a heading permalink anchor (``¶``/``#``/headerlink)."""
    if el.tag != "a":
        return False
    classes = (el.get("class") or "").lower()
    if "headerlink" in classes or "anchor" in classes or "permalink" in classes:
        return True
    text = normalize_text(el.text_content() or "")
    return text in {"¶", "#"}


def _clean_subtree(root: HtmlElement) -> None:
    """Remove chrome, copy controls and permalinks from ``root`` in place.

    Mutates the tree so the walker never sees nav/footer/aside, copy-code
    buttons or permalink anchors (SPEC §4). Iteration is over a materialized
    list because the tree is modified during traversal.
    """
    for el in list(root.iter()):
        tag = el.tag
        if not isinstance(tag, str):  # comments / PIs
            _drop(el)
            continue
        # <aside> is usually chrome, but some doc themes use it for real
        # admonitions (e.g. Starlight's <aside class="starlight-aside">); keep
        # those so their nested content survives.
        if tag == "aside" and _detect_admonition(el) is not None:
            continue
        if (
            tag in _CHROME_TAGS
            or _is_copy_control(el)
            or _is_permalink(el)
            or _is_chrome(el)
        ):
            _drop(el)


def _is_chrome(el: HtmlElement) -> bool:
    """True if ``el`` is a chrome container by role, class token or label.

    Removes navigation/feedback/ToC/footer widgets that live in generic
    ``<div>``s (not ``<nav>``/``<footer>``/``<aside>``) and would otherwise leak
    into block text — e.g. Material for MkDocs' ``md-feedback``/``md-sidebar`` or
    a "Was this page helpful?" prompt (SPEC §4; DECISIONS 2026-10-11). Matching is
    conservative: role and whole-class-token matches are structural and safe; the
    text-label fallback only fires on short elements so no real paragraph is
    dropped.
    """
    role = (el.get("role") or "").strip().lower()
    if role in _CHROME_ROLES:
        return True
    classes = {c for c in (el.get("class") or "").lower().split() if c}
    if classes & _CHROME_CLASS_TOKENS:
        return True
    text = normalize_text(el.text_content() or "").lower()
    if text and len(text) <= 40:
        return any(label in text for label in _CHROME_TEXT_LABELS)
    return False


def _drop(el: HtmlElement) -> None:
    """Detach ``el`` from its parent, discarding its tail text too."""
    parent = el.getparent()
    if parent is not None:
        el.tail = None
        parent.remove(el)


# --------------------------------------------------------------------------- #
# links / text helpers
# --------------------------------------------------------------------------- #
def _collect_links(el: HtmlElement, base_url: str) -> list[Link]:
    """Return every ``<a href>`` under ``el`` as a Link with an absolute href."""
    links: list[Link] = []
    for anchor in el.iter("a"):
        href = anchor.get("href")
        if not href:
            continue
        text = normalize_text(anchor.text_content() or "")
        links.append(Link(text=text, href=urljoin(base_url, href)))
    return links


def _block_text(el: HtmlElement) -> str:
    """Normalized, markup-free text content of ``el``."""
    return normalize_text(el.text_content() or "")


# --------------------------------------------------------------------------- #
# per-type data builders
# --------------------------------------------------------------------------- #
#: Class prefixes that name a code block's language (Prism/highlight.js/
#: Docusaurus/VitePress/Rouge ``language-``, Microsoft Learn ``lang-``, GitHub
#: ``highlight-source-``, MediaWiki SyntaxHighlight ``mw-highlight-lang-``).
#: ``hljs-`` is NOT one: ``hljs-keyword`` etc. are highlight.js token classes.
_LANGUAGE_PREFIXES = ("language-", "lang-", "highlight-source-", "mw-highlight-lang-")
#: Values that say "no particular language". Sphinx's ``default`` lexer guesses
#: Python and silently falls back to none, so it does not name a language.
_NO_LANGUAGE = frozenset({"default", "none", "nohighlight", "no-highlight"})
_LANGUAGE_RE = re.compile(r"^[a-z0-9][a-z0-9+#._-]{0,31}$")
#: How many ancestors of a ``<pre>`` may carry its language (Sphinx puts it two
#: levels up: ``div.highlight-python3 > div.highlight > pre``).
_LANGUAGE_ANCESTORS = 3


def _clean_language(value: str | None) -> str | None:
    """A lowercase language name, or ``None`` for empty/"no language" values."""
    low = (value or "").strip().strip(";").lower()
    if not low or low in _NO_LANGUAGE or not _LANGUAGE_RE.match(low):
        return None
    return low


def _language_of(node: HtmlElement) -> str | None:
    """The language one element declares by attribute or class, if any."""
    for attr in ("data-lang", "data-language"):
        found = _clean_language(node.get(attr))
        if found:
            return found
    classes = (node.get("class") or "").split()
    lows = [c.lower() for c in classes]
    for i, low in enumerate(lows):
        for prefix in _LANGUAGE_PREFIXES:
            if low.startswith(prefix):
                found = _clean_language(low[len(prefix) :])
                if found:
                    return found
        # SyntaxHighlighter (MDN): class="brush: js" or "brush:js".
        if low.startswith("brush:"):
            rest = low[len("brush:") :] or (lows[i + 1] if i + 1 < len(lows) else "")
            found = _clean_language(rest)
            if found:
                return found
        # Sphinx: <div class="highlight-python3 notranslate"><div class="highlight">.
        if low.startswith("highlight-") and _is_sphinx_highlight(node, lows):
            found = _clean_language(low[len("highlight-") :])
            if found:
                return found
    return None


def _is_sphinx_highlight(node: HtmlElement, classes: list[str]) -> bool:
    """True for Sphinx's ``div.highlight-<lang>`` wrapper, not other ``highlight-*``."""
    if "notranslate" in classes:
        return True
    return any(
        "highlight" in (child.get("class") or "").split()
        for child in node.iterchildren()
        if isinstance(child.tag, str)
    )


def _detect_language(el: HtmlElement) -> str | None:
    """Detect a code block's language (H6, M10b Phase P).

    Looks at the ``<pre>``, its ``<code>`` children, then up to
    ``_LANGUAGE_ANCESTORS`` ancestors that wrap no other ``<pre>`` (so a
    wrapper's language never spreads to a sibling block). Token spans inside
    the code are never read: their classes (``hljs-keyword``) are not
    languages.
    """
    nodes: list[HtmlElement] = [el, *el.iterchildren("code")]
    ancestor = el.getparent()
    for _ in range(_LANGUAGE_ANCESTORS):
        if ancestor is None or sum(1 for _ in ancestor.iter("pre")) > 1:
            break
        nodes.append(ancestor)
        ancestor = ancestor.getparent()
    for node in nodes:
        found = _language_of(node)
        if found:
            return found
    return None


#: Class tokens of line-number gutters inside code (Sphinx/Pygments inline
#: ``span.linenos``, ``.lineno``, Rouge ``.gutter``/``.rouge-gutter``,
#: highlight.js line-numbers plugin, Prism ``.line-numbers-rows``).
_LINENO_CLASSES = frozenset(
    {
        "linenos",
        "lineno",
        "linenodiv",
        "line-number",
        "line-numbers-rows",
        "gutter",
        "rouge-gutter",
        "hljs-ln-numbers",
        "hljs-ln-n",
    }
)
#: A run of this many line-start numbers 1, 2, 3... glued to the code is a
#: gutter even without a class (CSS-in-JS themes such as Twilio's).
_LINENO_MIN_RUN = 3
#: ... and it numbers at least this share of the block's lines.
_LINENO_LINE_SHARE = 0.8


def _is_lineno_element(node: HtmlElement) -> bool:
    classes = {c.lower() for c in (node.get("class") or "").split()}
    return bool(classes & _LINENO_CLASSES) or node.get("data-line-number") is not None


def _strip_line_numbers(el: HtmlElement) -> HtmlElement:
    """Return ``el`` (or a copy) without line-number gutter elements.

    Removes elements marked as gutters by class/attribute, and runs of
    unmarked leaf elements whose text is a consecutive line number placed at
    the start of a line and followed by code on that line (``1import os``).
    Code text itself is never altered; only whole gutter elements go, with
    their tails kept.
    """
    marked = [n for n in el.iter() if n is not el and _is_lineno_element(n)]
    unmarked = _unmarked_gutter(el) if not marked else []
    if not marked and not unmarked:
        return el
    copy = deepcopy(el)
    # deepcopy preserves document order, so map by position.
    originals = list(el.iter())
    copies = list(copy.iter())
    index = {id(n): i for i, n in enumerate(originals)}
    for node in [*marked, *unmarked]:
        target = copies[index[id(node)]]
        parent = target.getparent()
        if parent is None:
            continue
        tail = target.tail or ""
        previous = target.getprevious()
        if previous is not None:
            previous.tail = (previous.tail or "") + tail
        else:
            parent.text = (parent.text or "") + tail
        parent.remove(target)
    return copy


_BLOCK_LINE_TAGS = frozenset({"div", "p", "br", "tr", "li"})


def _unmarked_gutter(el: HtmlElement) -> list[HtmlElement]:
    """Leaf number elements forming a 1, 2, 3... line gutter (see above)."""
    # Walk text in order, tracking whether we are at the start of a line.
    candidates: list[tuple[HtmlElement, int]] = []
    glued: dict[int, bool] = {}
    state = {"line_start": True, "pending": -1}

    def feed_text(text: str) -> None:
        if not text:
            return
        pending = state["pending"]
        if pending >= 0 and text.strip():
            first_line = text.split("\n", 1)[0]
            glued[pending] = bool(first_line.strip())
            state["pending"] = -1
        elif pending >= 0 and "\n" in text:
            glued[pending] = False
            state["pending"] = -1
        if "\n" in text:
            state["line_start"] = not text.rsplit("\n", 1)[1].strip()
        elif text.strip():
            state["line_start"] = False

    def visit(node: HtmlElement) -> None:
        for child in node.iterchildren():
            if not isinstance(child.tag, str):
                feed_text(child.tail or "")
                continue
            if child.tag in _BLOCK_LINE_TAGS:
                state["line_start"] = True
            text = child.text_content() or ""
            is_leaf = not any(isinstance(c.tag, str) for c in child)
            stripped = text.strip()
            if (
                is_leaf
                and state["line_start"]
                and stripped.isdigit()
                and len(stripped) <= 5
            ):
                candidates.append((child, int(stripped)))
                state["pending"] = len(candidates) - 1
                state["line_start"] = False
            else:
                feed_text(child.text or "")
                visit(child)
            if child.tag in _BLOCK_LINE_TAGS:
                state["line_start"] = True
            feed_text(child.tail or "")

    feed_text(el.text or "")
    visit(el)

    found: list[HtmlElement] = []
    run: list[int] = []

    lines = len(_raw_code(el).strip("\n").splitlines()) or 1

    def close_run() -> None:
        # A gutter numbers (nearly) every line, and most numbers sit glued to
        # code; an output block "1\n2\n3" or a list literal does not qualify.
        if len(run) >= max(_LINENO_MIN_RUN, _LINENO_LINE_SHARE * lines) and sum(
            glued.get(i, False) for i in run
        ) * 2 >= len(run):
            found.extend(candidates[i][0] for i in run)

    for i, (_, number) in enumerate(candidates):
        if run and number == candidates[run[-1]][1] + 1:
            run.append(i)
        else:
            close_run()
            run = [i]
    close_run()
    return found


def _raw_code(el: HtmlElement) -> str:
    """Return the byte-for-byte text of a ``<pre>``/``<code>`` block.

    ``text_content`` concatenates descendant text verbatim (no whitespace
    normalization), which is exactly the SPEC §4 requirement for code. A single
    trailing newline that browsers add visually is left untouched to stay
    faithful to the source bytes.

    Line breaks a browser draws without a newline character are kept too: a
    ``<br>``, and each ``<div>``/``<p>`` line inside the block (the CodeMirror
    markup of react.dev). Without this, every line of such a snippet was glued
    into one (DECISIONS.md 2026-10-17).
    """
    if not any(sub is not el for sub in el.iter("br", "div", "p")):
        return el.text_content() or ""
    copy = deepcopy(el)
    for sub in copy.iter("br", "div", "p"):
        if sub is copy:
            continue
        tail = sub.tail or ""
        if sub.tag == "br":
            sub.tail = "\n" + tail
        elif not tail.startswith("\n") and not (sub.text_content() or "").endswith(
            "\n"
        ):
            sub.tail = "\n" + tail
    return copy.text_content() or ""


def _build_list(el: HtmlElement, ordered: bool, indent: int = 0) -> list[str]:
    """Flatten a ``<ul>``/``<ol>`` into indented item strings (SPEC §4).

    Nested lists contribute their own items indented by two extra spaces per
    nesting level. Only direct ``<li>`` children are items; a nested ``<ul>``/
    ``<ol>`` inside an ``<li>`` recurses.
    """
    items: list[str] = []
    prefix = "  " * indent
    for li in el.iterchildren("li"):
        own = normalize_text("".join(_direct_text(li)))
        items.append(prefix + own)
        for child in li.iterchildren("ul", "ol"):
            items.extend(_build_list(child, child.tag == "ol", indent + 1))
    return items


def _direct_text(li: HtmlElement) -> list[str]:
    """Text of ``li`` excluding any nested list markup."""
    parts: list[str] = []
    if li.text:
        parts.append(li.text)
    for child in li.iterchildren():
        if child.tag in {"ul", "ol"}:
            if child.tail:
                parts.append(child.tail)
            continue
        parts.append(child.text_content() or "")
        if child.tail:
            parts.append(child.tail)
    return parts


def _cell_text(cell: HtmlElement) -> str:
    """Normalized text of a single table cell."""
    return normalize_text(cell.text_content() or "")


def _build_table(el: HtmlElement) -> tuple[dict[str, object], bool]:
    """Build table ``data`` and report whether a header row was found (SPEC §4).

    Headers come from ``<thead>`` ``<th>``s, else the first row if it is all
    ``<th>``. Merged cells (``colspan``/``rowspan``) are repeated across every
    position they span. Returns ``(data, has_header)``; when ``has_header`` is
    False the caller appends a ``table_no_header`` warning.
    """
    caption_el = el.find("caption")
    caption = _cell_text(caption_el) if caption_el is not None else None

    rows_cells = _expand_rows(el)
    headers: list[str] = []
    body_rows = rows_cells

    thead = el.find("thead")
    if thead is not None:
        head_rows = _expand_rows(thead)
        if head_rows:
            headers = head_rows[0]
            body_rows = _expand_rows(el, skip=thead)
    elif rows_cells and _first_row_is_header(el):
        headers = rows_cells[0]
        body_rows = rows_cells[1:]

    data: dict[str, object] = {
        "caption": caption,
        "headers": headers,
        "rows": body_rows,
        "notes": [],
    }
    return data, bool(headers)


def _is_code_table(el: HtmlElement) -> bool:
    """True if ``el`` is a syntax-highlighter code table (gutter + code column).

    Pygments/MkDocs render highlighted code with line numbers as
    ``<table class="highlighttable">`` holding a ``td.linenos`` gutter and a
    ``td.code`` cell with the real ``<pre>``. These are code, not data, so the
    walker emits them as code blocks. Detected by the ``highlighttable`` class or
    by the presence of both a ``linenos`` and a ``code`` cell.
    """
    classes = (el.get("class") or "").lower().split()
    if "highlighttable" in classes:
        return True
    linenos_xpath = (
        './/td[contains(concat(" ", normalize-space(@class), " "), " linenos ")]'
    )
    code_xpath = './/td[contains(concat(" ", normalize-space(@class), " "), " code ")]'
    return bool(el.xpath(linenos_xpath)) and bool(el.xpath(code_xpath))


def _first_row_is_header(table: HtmlElement) -> bool:
    """True if the table's first row consists entirely of ``<th>`` cells."""
    for tr in table.iter("tr"):
        cells = [c for c in tr.iterchildren() if c.tag in {"td", "th"}]
        if not cells:
            continue
        return all(c.tag == "th" for c in cells)
    return False


def _expand_rows(
    scope: HtmlElement, skip: HtmlElement | None = None
) -> list[list[str]]:
    """Return the rows of ``scope`` as text grids, repeating merged cells.

    ``colspan`` repeats a cell horizontally; ``rowspan`` carries it down into
    the following rows at the same column. ``skip`` excludes a subtree (used to
    drop ``<thead>`` rows from the body pass). Deterministic: rows and columns
    follow document order.
    """
    skip_set = set(skip.iter()) if skip is not None else set()
    grid: list[list[str]] = []
    # Pending rowspans: column index -> (text, remaining rows).
    carried: dict[int, tuple[str, int]] = {}

    for tr in scope.iter("tr"):
        if tr in skip_set:
            continue
        row: list[str] = []
        col = 0
        cells = [c for c in tr.iterchildren() if c.tag in {"td", "th"}]
        cell_iter = iter(cells)
        # Walk columns, injecting carried rowspan values first.
        remaining = dict(carried)
        carried = {}
        pending_cell: HtmlElement | None = next(cell_iter, None)
        while pending_cell is not None or remaining:
            if col in remaining:
                text, left = remaining.pop(col)
                row.append(text)
                if left > 1:
                    carried[col] = (text, left - 1)
                col += 1
                continue
            if pending_cell is None:
                break
            text = _cell_text(pending_cell)
            colspan = _span(pending_cell, "colspan")
            rowspan = _span(pending_cell, "rowspan")
            for _ in range(colspan):
                row.append(text)
                if rowspan > 1:
                    carried[col] = (text, rowspan - 1)
                col += 1
            pending_cell = next(cell_iter, None)
        if row:
            grid.append(row)
    return grid


def _span(cell: HtmlElement, attr: str) -> int:
    """Parse a ``colspan``/``rowspan`` attribute, defaulting to 1."""
    raw = cell.get(attr)
    if not raw:
        return 1
    try:
        value = int(raw.strip())
    except ValueError:
        return 1
    return max(1, value)


def _detect_admonition(el: HtmlElement) -> str | None:
    """Return the admonition kind for ``el`` if it is one, else ``None``.

    Detection uses class names (``admonition``/``callout``/``alert``/``note``/
    ``warning`` and synonyms), ``role="note"``, and BEM-style modifier classes
    (``<base>--<kind>``, e.g. Starlight's ``starlight-aside starlight-aside--note``)
    (SPEC §4). The kind is the first canonical match among the classes; unknown
    kinds map to the closest of the five via ``_ADMONITION_SYNONYMS`` and
    otherwise default to ``note``.
    """
    classes = (el.get("class") or "").lower().split()
    role = (el.get("role") or "").strip().lower()
    class_set = set(classes)

    # BEM modifier suffixes like "starlight-aside--note" carry the kind after
    # the last "--"; collect those modifiers so both detection and the kind use
    # them. A base class ending in "-aside" (Starlight) also marks an admonition.
    modifiers = [cls.rsplit("--", 1)[-1] for cls in classes if "--" in cls]
    has_aside_base = any(cls.endswith("-aside") or cls == "aside" for cls in classes)

    is_admonition = (
        role == "note"
        or bool(class_set & {"admonition", "callout", "alert"})
        or bool(class_set & {"note", "warning", "tip", "danger", "info"})
        or any(c in _ADMONITION_SYNONYMS for c in classes)
        or has_aside_base
        or any(m in _ADMONITION_KINDS or m in _ADMONITION_SYNONYMS for m in modifiers)
    )
    if not is_admonition:
        return None

    for kind in _ADMONITION_KINDS:
        if kind in class_set or kind in modifiers:
            return kind
    for cls in (*classes, *modifiers):
        if cls in _ADMONITION_SYNONYMS:
            return _ADMONITION_SYNONYMS[cls]
    return "note"


def _build_definition_list(el: HtmlElement) -> dict[str, object]:
    """Build definition-list ``data`` (DECISIONS 2026-10-11): term/definition pairs."""
    items: list[dict[str, str]] = []
    current_term: str | None = None
    for child in el.iterchildren("dt", "dd"):
        if child.tag == "dt":
            current_term = _block_text(child)
        else:
            items.append({"term": current_term or "", "definition": _block_text(child)})
    return {"items": items}


# --------------------------------------------------------------------------- #
# walker
# --------------------------------------------------------------------------- #
class _Walker:
    """Stateful, deterministic walker emitting ordered blocks from a subtree."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url
        self.blocks: list[Block] = []
        self.warnings: list[Warning] = []
        self._heading_stack: list[tuple[int, str]] = []
        self._order = 0

    def _heading_path(self) -> list[str]:
        return [title for _, title in self._heading_stack]

    def _emit(
        self,
        block_type: str,
        el: HtmlElement,
        *,
        text: str,
        data: dict[str, object],
        level: int | None = None,
    ) -> Block:
        block = Block(
            block_id="",  # assigned later by assign_block_ids
            type=block_type,  # type: ignore[arg-type]
            order=self._order,
            level=level,
            heading_path=self._heading_path(),
            text=text,
            data=data,
            links=_collect_links(el, self.base_url),
            page=None,
        )
        self._order += 1
        self.blocks.append(block)
        return block

    def walk(self, root: HtmlElement) -> None:
        for child in root.iterchildren():
            self._visit(child)

    def _visit(self, el: HtmlElement) -> None:
        tag = el.tag
        if not isinstance(tag, str):
            return

        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._visit_heading(el, int(tag[1]))
            return
        if tag == "p":
            text = _block_text(el)
            if text:
                self._emit("paragraph", el, text=text, data={})
            return
        if tag in {"ul", "ol"}:
            self._visit_list(el, ordered=tag == "ol")
            return
        if tag == "pre":
            self._visit_code(el)
            return
        if tag == "table":
            self._visit_table(el)
            return
        if tag == "dl":
            self._visit_definition_list(el)
            return
        if tag == "blockquote":
            self._visit_blockquote(el)
            return

        # Admonition detection applies to generic containers before recursion.
        kind = _detect_admonition(el)
        if kind is not None:
            self._visit_admonition(el, kind)
            return

        if tag in _CONTAINER_TAGS:
            self.walk(el)
            return

        # Unknown / custom-element wrappers (e.g. <starlight-tabs>, custom web
        # components used by doc themes): if they contain element children,
        # descend like a container so nested structural blocks (code/table/...)
        # are not flattened into a single paragraph and lost (DECISIONS
        # 2026-10-11, "M3 — descida ..."). Only truly leaf elements with text
        # become a paragraph.
        if any(isinstance(c.tag, str) for c in el.iterchildren()):
            self.walk(el)
            return

        text = _block_text(el)
        if text:
            self._emit("paragraph", el, text=text, data={})

    def _visit_heading(self, el: HtmlElement, level: int) -> None:
        text = _block_text(el)
        # Pop stack entries at the same or deeper level, then push this one.
        while self._heading_stack and self._heading_stack[-1][0] >= level:
            self._heading_stack.pop()
        self._emit("heading", el, text=text, data={}, level=level)
        self._heading_stack.append((level, text))

    def _visit_code(self, el: HtmlElement) -> None:
        language = _detect_language(el)
        clean = _strip_line_numbers(el)
        inner = clean.find("code")
        code = _raw_code(inner if inner is not None else clean)
        self._emit(
            "code",
            el,
            text=code,
            data={"language": language, "code": code},
        )

    def _visit_table(self, el: HtmlElement) -> None:
        # Syntax-highlighter "code tables" (Pygments/MkDocs ``highlighttable``:
        # a line-number gutter column + a code column) are logically CODE, not a
        # data table. Emit the real code <pre> and skip the gutter so the code is
        # not lost to a table-text flatten (DECISIONS 2026-10-11).
        if _is_code_table(el):
            self._visit_code_table(el)
            return
        data, has_header = _build_table(el)
        self._emit("table", el, text=_block_text(el), data=data)
        if not has_header:
            self.warnings.append(
                Warning(
                    code="table_no_header",
                    message="table has no detectable header row",
                    block_id=None,  # paired to its block in _finalize_warnings
                )
            )

    def _visit_code_table(self, el: HtmlElement) -> None:
        """Emit the code <pre>s of a syntax-highlighter code table (gutter skipped)."""
        code_xpath = (
            './/td[contains(concat(" ", normalize-space(@class), " "), " code ")]'
        )
        for code_cell in el.xpath(code_xpath):
            for pre in code_cell.iter("pre"):
                self._visit_code(pre)

    def _visit_list(self, el: HtmlElement, *, ordered: bool) -> None:
        """Emit the list block AND any structural blocks nested in its items.

        The list text is flattened into ``data["items"]`` as before. But a
        ``<pre>``/``<table>`` placed inside a ``<li>`` (common in changelogs and
        API references) would be lost if we only flattened text, so after the
        list block we emit each item's nested structural blocks in document order
        (DECISIONS 2026-10-11, "M3 — descida em <dl>/<dd> ...", generalized to
        list items: nested structural blocks are never lost to a container).
        """
        items = _build_list(el, ordered)
        self._emit(
            "list",
            el,
            text=normalize_text(" ".join(items)),
            data={"ordered": ordered, "items": items},
        )
        for li in el.iterchildren("li"):
            for struct in _structural_descendants_in_item(li):
                self._visit(struct)

    def _visit_blockquote(self, el: HtmlElement) -> None:
        """Emit the blockquote block plus any structural blocks nested in it."""
        text = _block_text(el)
        self._emit("blockquote", el, text=text, data={"text": text})
        for struct in _structural_descendants(el):
            self._visit(struct)

    def _visit_admonition(self, el: HtmlElement, kind: str) -> None:
        """Emit the admonition block plus any structural blocks nested in it.

        Admonitions (note/warning/tip callouts, including Starlight
        ``<aside class="starlight-aside">`` and MkDocs ``<details class="info">``)
        regularly wrap code samples and tables. Flattening them to the
        admonition's text would drop those from the IR, so nested structural
        blocks are emitted as their own blocks in document order (DECISIONS
        2026-10-11).
        """
        self._emit("admonition", el, text=_block_text(el), data={"kind": kind})
        for struct in _structural_descendants(el):
            self._visit(struct)

    def _visit_definition_list(self, el: HtmlElement) -> None:
        """Emit the definition_list block AND any structural blocks nested in it.

        The term/definition text pairs are captured in the ``definition_list``
        block's ``data`` as before (DECISIONS 2026-10-11, blockquote/dl ``data``
        shape). But API-reference pages routinely put ``<pre>``/``<table>``/lists
        INSIDE ``<dd>`` elements; flattening those to text would drop them from
        the IR and break the 100%-coverage guarantee. So after emitting the
        definition_list we descend into each ``<dd>`` and emit every nested
        structural block (code, table, list, nested dl, blockquote, admonition)
        as its own block in document order (DECISIONS 2026-10-11, "M3 — descida em
        <dl>/<dd> para preservar blocos estruturais aninhados").
        """
        data = _build_definition_list(el)
        self._emit("definition_list", el, text=_block_text(el), data=data)
        for dd in el.iterchildren("dd"):
            self._emit_nested_structural(dd)

    def _emit_nested_structural(self, dd: HtmlElement) -> None:
        """Emit structural blocks (code/table/list/dl) nested inside a ``<dd>``.

        Only the structural descendants are emitted — the plain term/definition
        prose is already captured in the definition_list block's ``data``, so
        emitting it again would duplicate text. Each structural element is
        emitted at its OUTERMOST position (its topmost structural ancestor within
        the ``<dd>``), in document order, and its own subtree is not re-scanned so
        a nested structure is emitted once by its handler. Nested ``<dl>`` recurse
        through ``_visit`` so their own nested structural blocks are preserved
        too.
        """
        for struct in _structural_descendants(dd):
            self._visit(struct)


def _outermost_structural(
    root: HtmlElement, structural: frozenset[str], *, barrier: frozenset[str]
) -> list[HtmlElement]:
    """Return the outermost ``structural`` descendants of ``root``, in order.

    Only the OUTERMOST matching elements are returned: once one is found its own
    subtree is left to that block's own handler (no double emission). Descent
    stops at ``barrier`` tags (sub-containers whose content is captured
    elsewhere), so e.g. a nested list already flattened into the item text is not
    re-scanned. Deterministic: document order.
    """
    found: list[HtmlElement] = []

    def _descend(node: HtmlElement) -> None:
        for child in node.iterchildren():
            tag = child.tag
            if not isinstance(tag, str):
                continue
            if tag in structural:
                found.append(child)
            elif tag in barrier:
                continue
            else:
                _descend(child)

    _descend(root)
    return found


# Structural blocks nested in a <dd>: everything structural, including sub-lists
# (a <dd> is prose, not a flattened list, so its nested lists are real blocks).
_DD_STRUCTURAL = frozenset({"pre", "table", "ul", "ol", "dl"})
# Structural blocks nested in a <li>: code/table/dl only. Nested <ul>/<ol> are
# already flattened into the list's items text, so they are barriers (not
# re-emitted) rather than new blocks.
_LI_STRUCTURAL = frozenset({"pre", "table", "dl"})
_LI_BARRIER = frozenset({"ul", "ol"})


def _structural_descendants(root: HtmlElement) -> list[HtmlElement]:
    """Outermost structural blocks nested in a ``<dd>`` (see _outermost_structural)."""
    return _outermost_structural(root, _DD_STRUCTURAL, barrier=frozenset())


def _structural_descendants_in_item(li: HtmlElement) -> list[HtmlElement]:
    """Outermost code/table/dl blocks nested inside a ``<li>`` (sub-lists excluded)."""
    return _outermost_structural(li, _LI_STRUCTURAL, barrier=_LI_BARRIER)


# --------------------------------------------------------------------------- #
# main-content selection + fallback
# --------------------------------------------------------------------------- #
def _find_main(tree: HtmlElement) -> HtmlElement | None:
    """Return the main-content subtree, if one can be located (SPEC §4 + DECISIONS).

    Selection is DETERMINISTIC and precedence-ordered (DECISIONS 2026-10-11,
    "M3 — seleção de conteúdo principal robusta"):

    1. the first semantic ``<main>`` then ``<article>`` element;
    2. the first element with ``role="main"`` (Sphinx renders the body as
       ``<div class="body" role="main">`` and has NO ``<main>`` tag, so without
       this the mandatory readability fallback could never fire for those pages);
    3. known Sphinx/MkDocs main-content containers by class, as a last resort
       (``div.document``, ``article.md-content__inner``, ``main.md-main__inner``,
       ``div.body``).

    Returning a real subtree here is what lets the fallback (``extract_html``)
    re-walk the original main content and guarantee 100% of its ``<table>`` and
    ``<pre>`` reach the IR.
    """
    for tag in ("main", "article"):
        found = tree.find(f".//{tag}")
        if found is not None:
            return found

    # role="main" (Sphinx and many themes mark the body container this way).
    role_main = tree.xpath('.//*[@role="main"]')
    if role_main:
        return role_main[0]

    # Known main-content containers by class, in precedence order.
    for xpath in (
        './/article[contains(concat(" ", normalize-space(@class), " "),'
        ' " md-content__inner ")]',
        './/main[contains(concat(" ", normalize-space(@class), " "),'
        ' " md-main__inner ")]',
        './/div[contains(concat(" ", normalize-space(@class), " "), " document ")]',
        './/div[contains(concat(" ", normalize-space(@class), " "), " body ")]',
    ):
        found_list = tree.xpath(xpath)
        if found_list:
            return found_list[0]
    return None


#: A child holding this share of its parent's content text is the content.
_DOMINANT_SHARE = 0.8
#: Readability keeping less than this share of the container's text lost content
#: (0.5 missed PostgreSQL pages where it kept only the largest table, ~54%).
_READABILITY_MIN_SHARE = 0.8


def _content_chars(el: HtmlElement, sizes: dict[HtmlElement, int]) -> int:
    """Characters of text under ``el``, chrome subtrees excluded; memoized."""
    if el in sizes:
        return sizes[el]
    if not isinstance(el.tag, str) or el.tag in _CHROME_TAGS or _is_chrome(el):
        n = 0
    else:
        n = len(normalize_text(el.text or "")) + sum(
            _content_chars(child, sizes) + len(normalize_text(child.tail or ""))
            for child in el
        )
    sizes[el] = n
    return n


def _dominant_container(
    tree: HtmlElement, sizes: dict[HtmlElement, int]
) -> HtmlElement:
    """The deepest element holding most of the page's content text.

    For pages with no ``<main>``/``<article>``/``role="main"`` (PostgreSQL,
    SQLite, RFC Editor docs): descend from ``<body>`` while one child holds at
    least ``_DOMINANT_SHARE`` of its parent's non-chrome text (DECISIONS.md
    2026-10-16, M10a Fase 3 item 4).
    """
    bodies = tree.xpath("//body")
    node: HtmlElement = bodies[0] if bodies else tree
    while True:
        children = [c for c in node if isinstance(c.tag, str)]
        if not children:
            return node
        best = max(children, key=lambda c: _content_chars(c, sizes))
        if _content_chars(best, sizes) < _DOMINANT_SHARE * _content_chars(node, sizes):
            return node
        node = best


def _count_tag(el: HtmlElement | None, tag: str) -> int:
    """Count descendants (and self) with ``tag`` under ``el``."""
    if el is None:
        return 0
    return sum(1 for _ in el.iter(tag))


# Structural block tags that readability may drop and that MUST reach the IR.
_FALLBACK_TAGS = ("table", "pre", "ul", "ol", "dl")


def _structural_counts(el: HtmlElement | None) -> dict[str, int]:
    """Count fallback-relevant structural tags under ``el`` (SPEC §4 + DECISIONS)."""
    return {tag: _count_tag(el, tag) for tag in _FALLBACK_TAGS}


def _readability_subtree(text: str) -> HtmlElement:
    """Return the readability-isolated main content as an lxml subtree."""
    summary_html = ReadabilityDocument(text).summary(html_partial=True)
    return lxml.html.fromstring(summary_html)


# --------------------------------------------------------------------------- #
# public API
# --------------------------------------------------------------------------- #
def extract_html(raw: bytes | str, source: SourceInfo) -> Document:
    """Extract a :class:`Document` from raw HTML (SPEC §4).

    ``raw`` is the fetched body (bytes decoded via the Content-Type charset) or
    decoded text; ``source`` supplies ``final_url`` (link/base resolution),
    ``url`` (document id) and ``content_hash`` (version id). The returned
    Document's blocks carry stable ``block_id``s and the SPEC §4 ``data`` shapes;
    its warnings surface ``table_no_header`` and the mandatory
    ``readability_fallback``.
    """
    text = _ensure_text(raw, source)
    full_tree = (
        lxml.html.fromstring(text) if text.strip() else lxml.html.Element("html")
    )

    # Original main/article, measured BEFORE cleanup, for the fallback check.
    # Beyond the SPEC's table/<pre> trigger, readability also drops <ul>/<ol>/<dl>
    # (DECISIONS 2026-10-11, "M3 — gatilho de readability_fallback estendido"),
    # so those counts arm the fallback too.
    original_main = _find_main(full_tree)
    orig_counts = _structural_counts(original_main)

    readability_subtree = _readability_subtree(text)
    used_fallback = False

    if original_main is not None and any(orig_counts.values()):
        ra_counts = _structural_counts(readability_subtree)
        if any(ra_counts[tag] < orig_counts[tag] for tag in orig_counts):
            used_fallback = True

    # No main container to compare against: readability may keep a single block
    # of a long page (one table of a PostgreSQL function chapter). Compare its
    # text with the page's dominant content container instead.
    lost_text = False
    if original_main is None and text.strip():
        sizes: dict[HtmlElement, int] = {}
        container = _dominant_container(full_tree, sizes)
        kept = len(normalize_text(readability_subtree.text_content() or ""))
        if kept < _READABILITY_MIN_SHARE * _content_chars(container, sizes):
            original_main, used_fallback, lost_text = container, True, True

    content_root = original_main if used_fallback else readability_subtree

    _clean_subtree(content_root)
    walker = _Walker(source.final_url)
    walker.walk(content_root)

    assign_block_ids(walker.blocks)
    warnings = _finalize_warnings(walker)
    if used_fallback:
        warnings.append(
            Warning(
                code="readability_fallback",
                message=(
                    "readability kept under 80% of the page's main text; "
                    "re-extracted from its dominant content container"
                    if lost_text
                    else "readability dropped main-content tables or code; "
                    "re-extracted from <main>/<article>"
                ),
                block_id=None,
            )
        )

    metadata = extract_metadata(text, source)
    return Document(
        document_id=make_document_id(source.url),
        version_id=make_version_id(source.content_hash),
        source=source,
        metadata=metadata,
        blocks=walker.blocks,
        warnings=warnings,
    )


def _finalize_warnings(walker: _Walker) -> list[Warning]:
    """Attach block_ids to ``table_no_header`` warnings now that ids are assigned.

    The walker emits one ``table_no_header`` warning per headerless table, in
    document order. Here each is paired to its table block by scanning the
    emitted blocks in the same order, so every warning's ``block_id`` points at
    the right block. Deterministic: both lists follow document order.
    """
    # Map each headerless-table warning to its block in document order.
    headerless_warnings = [w for w in walker.warnings if w.code == "table_no_header"]
    if not headerless_warnings:
        return list(walker.warnings)

    headerless_blocks = [
        b for b in walker.blocks if b.type == "table" and not b.data.get("headers")
    ]
    finalized: list[Warning] = []
    idx = 0
    for warning in walker.warnings:
        if warning.code == "table_no_header" and idx < len(headerless_blocks):
            finalized.append(
                Warning(
                    code=warning.code,
                    message=warning.message,
                    block_id=headerless_blocks[idx].block_id,
                )
            )
            idx += 1
        else:
            finalized.append(warning)
    return finalized
