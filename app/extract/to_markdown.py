"""IR → Markdown rendering (SPEC §4).

``to_markdown`` serializes a :class:`~app.extract.ir.Document` to Markdown built
ALWAYS from the IR, never from the original HTML (SPEC §4). Each block type maps
to a stable Markdown construct:

- ``heading``        → ``#`` repeated ``level`` times + text.
- ``paragraph``      → the text as-is.
- ``list``           → ``- ``/``1. `` items, preserving the 2-space-indent
  nesting already encoded in ``data["items"]``.
- ``code``           → a fenced block carrying the detected language.
- ``table``          → a GFM table (header row + separator; a synthesized header
  when ``data["headers"]`` is empty).
- ``blockquote``     → ``> ``-prefixed lines.
- ``admonition``     → a GitHub-style ``> [!KIND]`` callout.
- ``definition_list``→ bolded term lines followed by their definitions.

Output is deterministic with stable spacing: blocks are separated by a single
blank line and there are no timestamps or unordered iteration. Code is emitted
byte-for-byte from ``data["code"]``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from app.extract.ir import Block, Document

_ADMONITION_LABELS = {
    "note": "NOTE",
    "warning": "WARNING",
    "tip": "TIP",
    "danger": "CAUTION",
    "info": "IMPORTANT",
}


def to_markdown(doc: Document) -> str:
    """Render ``doc`` to Markdown from the IR only (SPEC §4)."""
    rendered = [_render_block(block) for block in doc.blocks]
    return "\n\n".join(chunk for chunk in rendered if chunk)


def _render_block(block: Block) -> str:
    if block.type == "heading":
        level = block.level or 1
        return f"{'#' * level} {block.text}"
    if block.type == "paragraph":
        return block.text
    if block.type == "list":
        return _render_list(block)
    if block.type == "code":
        return _render_code(block)
    if block.type == "table":
        return _render_table(block)
    if block.type == "blockquote":
        return _render_blockquote(block.text)
    if block.type == "admonition":
        return _render_admonition(block)
    if block.type == "definition_list":
        return _render_definition_list(block)
    return block.text


def _render_list(block: Block) -> str:
    ordered = bool(block.data.get("ordered"))
    items = cast("list[str]", block.data.get("items", []))
    lines: list[str] = []
    for item in items:
        stripped = item.lstrip(" ")
        indent = item[: len(item) - len(stripped)]
        marker = "1. " if ordered else "- "
        lines.append(f"{indent}{marker}{stripped}")
    return "\n".join(lines)


def _render_code(block: Block) -> str:
    language = block.data.get("language")
    code = cast("str", block.data.get("code", ""))
    lang = language if isinstance(language, str) else ""
    body = code.rstrip("\n")
    return f"```{lang}\n{body}\n```"


def _render_table(block: Block) -> str:
    headers = cast("list[str]", block.data.get("headers", []))
    rows = cast("list[list[str]]", block.data.get("rows", []))
    caption = block.data.get("caption")

    width = _table_width(headers, rows)
    if width == 0:
        return ""

    if headers:
        header_cells = _pad(headers, width)
    else:
        # Synthesize a minimal header so the GFM table stays well-formed.
        header_cells = [f"col{i + 1}" for i in range(width)]

    lines = [_row(header_cells), _row(["---"] * width)]
    lines.extend(_row(_pad(r, width)) for r in rows)

    table = "\n".join(lines)
    if isinstance(caption, str) and caption:
        return f"{table}\n\n*{caption}*"
    return table


def _table_width(headers: list[str], rows: list[list[str]]) -> int:
    widths = [len(headers), *(len(r) for r in rows)]
    return max(widths) if widths else 0


def _pad(cells: list[str], width: int) -> list[str]:
    return [*cells, *([""] * (width - len(cells)))]


def _row(cells: list[str]) -> str:
    escaped = [c.replace("|", "\\|") for c in cells]
    return "| " + " | ".join(escaped) + " |"


def _render_blockquote(text: str) -> str:
    lines = text.split("\n") if text else [""]
    return "\n".join(f"> {line}" if line else ">" for line in lines)


def _render_admonition(block: Block) -> str:
    kind = block.data.get("kind")
    key = kind if isinstance(kind, str) else "note"
    label = _ADMONITION_LABELS.get(key, "NOTE")
    body = block.text
    if not body:
        return f"> [!{label}]"
    body_lines = "\n".join(f"> {line}" for line in body.split("\n"))
    return f"> [!{label}]\n{body_lines}"


def _render_definition_list(block: Block) -> str:
    items = cast("list[dict[str, str]]", block.data.get("items", []))
    lines: list[str] = []
    for item in items:
        term = item.get("term", "")
        definition = item.get("definition", "")
        lines.append(f"**{term}**")
        lines.append(f": {definition}")
    return "\n".join(lines)
