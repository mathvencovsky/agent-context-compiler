"""PDF -> IR extraction (SPEC §4).

``extract_pdf`` turns the raw bytes of a PDF into a
:class:`~app.extract.ir.Document`, page by page, using pdfplumber (MIT; pulls
pdfminer.six MIT/BSD). Everything here is DETERMINISTIC and pure — no LLM, no
network, no OCR (CLAUDE.md). The pipeline mirrors SPEC §4 ("Extração PDF"):

1. Open the bytes with ``pdfplumber.open(io.BytesIO(raw))``.
2. For every page (numbered from 1), group the page's characters into visual
   LINES by their vertical position, recording each line's dominant font size,
   whether it is monospace, and its vertical band (top/bottom) on the page.
3. Detect and DROP repeated running headers/footers: a normalized line that
   appears in the top or bottom band of MORE THAN 50% of the pages is page
   chrome, not content, and is removed before any block is built.
4. Infer the BODY font size as the most common rounded font size across all
   surviving lines. Headings are the two largest sizes strictly above the body
   size: the largest maps to ``level`` 1 and the next to ``level`` 2 (SPEC §4,
   "os 2 maiores tamanhos acima do corpo"). Those lines become ``heading``
   blocks carrying ``level``; the heading stack feeds every block's
   ``heading_path``.
5. Tables come from ``page.extract_tables()`` -> ``table`` blocks with the
   SPEC §4 ``data`` shape ``{caption, headers, rows, notes}``. When no header
   row is detectable the headers are empty and a ``table_no_header`` warning is
   emitted and paired to its block (mirroring ``html.py``'s finalization). Lines
   that fall inside a detected table's bounding box are not re-emitted as
   paragraphs, so table text is not duplicated.
6. Remaining lines become ``paragraph`` blocks, except a run of consecutive
   monospace lines which is emitted as a single ``code`` block (a simple,
   deterministic font heuristic — see DECISIONS.md 2026-10-11). Code text is
   preserved verbatim; every other block's text is whitespace-normalized via
   ``normalize_text``.
7. Every block carries its 1-based ``page``. ``assign_block_ids`` runs ONCE
   after all blocks are built. A ``low_text_content`` warning is added when the
   total extracted main text is below ``settings.render_min_text_chars``. A PDF
   with NO extractable text at all raises :class:`ExtractionEmpty` (HTTP 422);
   OCR is out of scope.

Header/footer bands: a line belongs to the TOP band when its top edge is within
the first ``_BAND_FRACTION`` (15%) of the page height, and to the BOTTOM band
when its bottom edge is within the last ``_BAND_FRACTION`` of the page height.
Only lines in these bands are candidates for repeated-chrome removal; body lines
are never dropped by the >50%-of-pages rule.
"""

from __future__ import annotations

import io
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import pdfplumber

from app.config import Settings, get_settings
from app.extract.invisible import invisible_text_warning, strip_invisible_blocks
from app.extract.ir import (
    Block,
    Document,
    Metadata,
    Warning,
    assign_block_ids,
    make_document_id,
    make_version_id,
    normalize_text,
)
from app.extract.pdf_limits import DecodeBudget, decode_budget
from app.fetch.errors import ExtractionEmpty

if TYPE_CHECKING:
    from app.extract.ir import SourceInfo

# Fraction of the page height that counts as the top (header) or bottom
# (footer) band for repeated-chrome detection.
_BAND_FRACTION = 0.15

# A running header/footer must repeat on strictly MORE THAN this fraction of the
# pages to be dropped (SPEC §4: "mais de 50% das páginas").
_REPEAT_THRESHOLD = 0.5

# Tolerance (in points) for grouping characters onto the same visual line by
# their vertical position.
_LINE_TOLERANCE = 3.0


@dataclass
class _Line:
    """One visual line of a page: its text, geometry and font traits."""

    text: str
    top: float
    bottom: float
    size: float
    monospace: bool
    page_number: int


@dataclass
class _PageLines:
    """The surviving (post-chrome-removal) lines of a single page."""

    page_number: int
    lines: list[_Line] = field(default_factory=list)
    tables: list[list[list[str | None]]] = field(default_factory=list)
    table_boxes: list[tuple[float, float, float, float]] = field(default_factory=list)


def _round_size(size: float) -> float:
    """Round a font size to one decimal so near-equal sizes group together."""
    return round(size, 1)


def _is_monospace(fontname: str) -> bool:
    """Heuristic: a font whose name marks it as monospaced (code-like).

    Deterministic substring match on the PostScript font name (DECISIONS.md
    2026-10-11). Catches the common families used for code in technical PDFs
    (Courier, Mono, Consolas, Menlo) without needing glyph-width analysis.
    """
    low = fontname.lower()
    return any(token in low for token in ("courier", "mono", "consol", "menlo"))


def _group_lines(page: pdfplumber.page.Page) -> list[_Line]:
    """Group a page's characters into visual lines, top-to-bottom, left-to-right.

    Characters are bucketed by their ``top`` coordinate (within
    ``_LINE_TOLERANCE`` points), then each bucket is ordered left-to-right by
    ``x0`` and concatenated. The line's font size and monospace flag come from
    the most common char size / fontname on the line, so a stray glyph does not
    skew the classification. Deterministic throughout.
    """
    chars = page.chars
    if not chars:
        return []

    ordered = sorted(chars, key=lambda c: (round(float(c["top"]), 1), float(c["x0"])))
    lines: list[_Line] = []
    bucket: list[dict[str, Any]] = []
    bucket_top: float | None = None

    def _flush(group: list[dict[str, Any]]) -> None:
        if not group:
            return
        group_sorted = sorted(group, key=lambda c: float(c["x0"]))
        text = "".join(str(c.get("text", "")) for c in group_sorted)
        if not text.strip():
            return
        sizes = Counter(_round_size(float(c["size"])) for c in group_sorted)
        fonts = Counter(str(c.get("fontname", "")) for c in group_sorted)
        dominant_size = sizes.most_common(1)[0][0]
        dominant_font = fonts.most_common(1)[0][0]
        tops = [float(c["top"]) for c in group_sorted]
        bottoms = [float(c["bottom"]) for c in group_sorted]
        lines.append(
            _Line(
                text=text,
                top=min(tops),
                bottom=max(bottoms),
                size=dominant_size,
                monospace=_is_monospace(dominant_font),
                page_number=page.page_number,
            )
        )

    for char in ordered:
        top = round(float(char["top"]), 1)
        if bucket_top is None or abs(top - bucket_top) <= _LINE_TOLERANCE:
            bucket.append(char)
            bucket_top = top if bucket_top is None else bucket_top
        else:
            _flush(bucket)
            bucket = [char]
            bucket_top = top
    _flush(bucket)
    return lines


def _in_band(line: _Line, page_height: float) -> str | None:
    """Return 'top'/'bottom' if the line sits in a header/footer band, else None."""
    band = page_height * _BAND_FRACTION
    if line.top <= band:
        return "top"
    if line.bottom >= page_height - band:
        return "bottom"
    return None


_PAGE_NUMBER_RE = re.compile(r"(?:^\s*\d+\s+)|(?:\s+\d+\s*$)|(?:^\s*\d+\s*$)")


def _chrome_key(text: str) -> str:
    """Normalize a band line for repeated-chrome comparison (SPEC §4).

    Running footers routinely carry a VARYING page number ("Page 1", "... 2"),
    so a raw text comparison would never see them as repeated. The page-number
    token (a standalone run of digits at the start or end of the line) is dropped
    before comparison so the stable chrome text matches across pages (DECISIONS
    2026-10-11). Deterministic and conservative: only leading/trailing bare
    integers are stripped, never digits embedded in words.
    """
    stripped = _PAGE_NUMBER_RE.sub(" ", normalize_text(text))
    return normalize_text(stripped)


def _repeated_chrome(
    page_lines: list[tuple[int, list[_Line], float]],
) -> set[str]:
    """Return chrome keys that repeat as header/footer on >50% of pages.

    A candidate is a band line (top or bottom band of a page) reduced to its
    :func:`_chrome_key` (page number stripped). Counting is per-page (a line
    repeated twice on one page counts once), so the threshold reflects how many
    PAGES share the chrome. Deterministic: operates on a set of normalized keys.
    """
    total_pages = len(page_lines)
    if total_pages <= 1:
        return set()

    pages_with_text: Counter[str] = Counter()
    for _page_number, lines, page_height in page_lines:
        seen_on_page: set[str] = set()
        for line in lines:
            if _in_band(line, page_height) is None:
                continue
            key = _chrome_key(line.text)
            if key:
                seen_on_page.add(key)
        pages_with_text.update(seen_on_page)

    threshold = total_pages * _REPEAT_THRESHOLD
    return {text for text, count in pages_with_text.items() if count > threshold}


def _table_box(table: Any) -> tuple[float, float, float, float]:
    """Return the (top, bottom, x0, x1) bounding box of a pdfplumber Table."""
    x0, top, x1, bottom = table.bbox
    return (float(top), float(bottom), float(x0), float(x1))


def _line_in_any_box(
    line: _Line, boxes: list[tuple[float, float, float, float]]
) -> bool:
    """True if the line's vertical center falls inside a table's bounding box."""
    center = (line.top + line.bottom) / 2.0
    return any(top <= center <= bottom for top, bottom, _x0, _x1 in boxes)


def _build_table_data(rows: list[list[str | None]]) -> tuple[dict[str, Any], bool]:
    """Build the SPEC §4 table ``data`` and report whether a header was found.

    pdfplumber returns a grid of cells (``None`` for empty cells). The first row
    is treated as the header when it is fully populated (every cell has text) and
    there is at least one body row; otherwise headers are empty and the caller
    emits a ``table_no_header`` warning. Cells are whitespace-normalized.
    """
    grid = [[normalize_text(cell or "") for cell in row] for row in rows]
    grid = [row for row in grid if any(cell for cell in row)]

    headers: list[str] = []
    body = grid
    if len(grid) >= 2 and all(cell for cell in grid[0]):
        headers = grid[0]
        body = grid[1:]

    data: dict[str, Any] = {
        "caption": None,
        "headers": headers,
        "rows": body,
        "notes": [],
    }
    return data, bool(headers)


class _Builder:
    """Deterministic builder turning per-page lines/tables into IR blocks."""

    def __init__(self, body_size: float, heading_levels: dict[float, int]) -> None:
        self._body_size = body_size
        self._heading_levels = heading_levels
        self.blocks: list[Block] = []
        self.warnings: list[Warning] = []
        self._heading_stack: list[tuple[int, str]] = []
        self._order = 0

    def _heading_path(self) -> list[str]:
        return [title for _, title in self._heading_stack]

    def _emit(
        self,
        block_type: str,
        *,
        text: str,
        data: dict[str, Any],
        page: int,
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
            links=[],
            page=page,
        )
        self._order += 1
        self.blocks.append(block)
        return block

    def _emit_heading(self, line: _Line, level: int) -> None:
        text = normalize_text(line.text)
        if not text:
            return
        while self._heading_stack and self._heading_stack[-1][0] >= level:
            self._heading_stack.pop()
        self._emit("heading", text=text, data={}, page=line.page_number, level=level)
        self._heading_stack.append((level, text))

    def _emit_code(self, lines: list[_Line]) -> None:
        code = "\n".join(line.text for line in lines)
        if not code.strip():
            return
        self._emit(
            "code",
            text=code,
            data={"language": None, "code": code},
            page=lines[0].page_number,
        )

    def _emit_paragraph(self, line: _Line) -> None:
        text = normalize_text(line.text)
        if text:
            self._emit("paragraph", text=text, data={}, page=line.page_number)

    def _emit_table(self, data: dict[str, Any], has_header: bool, page: int) -> None:
        text = normalize_text(
            " ".join(
                cell for row in [data["headers"], *data["rows"]] for cell in row if cell
            )
        )
        self._emit("table", text=text, data=data, page=page)
        if not has_header:
            self.warnings.append(
                Warning(
                    code="table_no_header",
                    message="table has no detectable header row",
                    block_id=None,  # paired to its block in finalize_warnings
                )
            )

    def consume_page(self, page: _PageLines) -> None:
        """Emit blocks for one page: headings, tables, paragraphs and code.

        Tables are emitted first in page order relative to the lines they
        overlap. To keep the output deterministic and the code heuristic simple,
        we emit the page's tables, then walk the non-table lines top-to-bottom,
        grouping consecutive monospace lines into a single code block and every
        other line into a paragraph (or a heading, by font size).
        """
        for data, has_header in (_build_table_data(table) for table in page.tables):
            self._emit_table(data, has_header, page.page_number)

        content_lines = [
            line for line in page.lines if not _line_in_any_box(line, page.table_boxes)
        ]

        pending_code: list[_Line] = []
        for line in content_lines:
            level = self._heading_levels.get(line.size)
            if level is not None:
                self._flush_code(pending_code)
                pending_code = []
                self._emit_heading(line, level)
                continue
            if line.monospace:
                pending_code.append(line)
                continue
            self._flush_code(pending_code)
            pending_code = []
            self._emit_paragraph(line)
        self._flush_code(pending_code)

    def _flush_code(self, lines: list[_Line]) -> None:
        if lines:
            self._emit_code(lines)


def _finalize_warnings(builder: _Builder) -> list[Warning]:
    """Pair each ``table_no_header`` warning to its block, now that ids exist.

    Mirrors ``html.py``: the builder emits one ``table_no_header`` per headerless
    table in document order; here each is matched to its headerless ``table``
    block in the same order. Deterministic.
    """
    headerless_warnings = [w for w in builder.warnings if w.code == "table_no_header"]
    if not headerless_warnings:
        return list(builder.warnings)

    headerless_blocks = [
        b for b in builder.blocks if b.type == "table" and not b.data.get("headers")
    ]
    finalized: list[Warning] = []
    idx = 0
    for warning in builder.warnings:
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


def _infer_heading_levels(lines: list[_Line]) -> tuple[float, dict[float, int]]:
    """Return the body font size and the size->heading-level map (SPEC §4).

    The body size is the most common rounded font size across all surviving
    lines (ties broken toward the smaller size for stability). Heading levels are
    the two largest sizes strictly ABOVE the body size: largest -> level 1, next
    -> level 2. Documents with no size above body yield an empty map (all lines
    become paragraphs).
    """
    size_counts: Counter[float] = Counter(line.size for line in lines)
    if not size_counts:
        return 0.0, {}

    # Most common size; break ties toward the smaller size for determinism.
    max_count = max(size_counts.values())
    body_size = min(size for size, count in size_counts.items() if count == max_count)

    above = sorted((size for size in size_counts if size > body_size), reverse=True)
    levels: dict[float, int] = {}
    for level, size in enumerate(above[:2], start=1):
        levels[size] = level
    return body_size, levels


#: Warning code when a PDF resource limit truncated the extraction (audit A2).
PDF_TRUNCATED_WARNING_CODE = "pdf_truncated"


def _limit_warnings(
    settings: Settings, truncated_pages: int, budget: DecodeBudget
) -> list[Warning]:
    """Say explicitly when a resource limit cut the extraction short."""
    warnings: list[Warning] = []
    if truncated_pages:
        warnings.append(
            Warning(
                code=PDF_TRUNCATED_WARNING_CODE,
                message=(
                    f"only the first {settings.pdf_max_pages} pages were extracted; "
                    f"{truncated_pages} more pages were skipped (page limit)"
                ),
                block_id=None,
            )
        )
    if budget.truncated_streams:
        warnings.append(
            Warning(
                code=PDF_TRUNCATED_WARNING_CODE,
                message=(
                    f"{budget.truncated_streams} compressed PDF stream(s) exceeded "
                    "the decoded-size limit and were truncated; content may be "
                    "missing"
                ),
                block_id=None,
            )
        )
    return warnings


def extract_pdf(
    raw: bytes, source: SourceInfo, *, settings: Settings | None = None
) -> Document:
    """Extract a :class:`Document` from the raw bytes of a PDF (SPEC §4).

    ``raw`` is the fetched PDF body; ``source`` supplies ``url`` (document id),
    ``content_hash`` (version id) and is expected to carry ``render_mode='pdf'``.
    Extraction is deterministic and page-ordered; every block carries its 1-based
    ``page``. Repeated running headers/footers are removed, headings are inferred
    from relative font size, and tables come from ``extract_tables``.

    Raises :class:`~app.fetch.errors.ExtractionEmpty` (HTTP 422) when the PDF has
    no extractable text at all (e.g. a scanned/image-only PDF). OCR is out of
    scope for M4.
    """
    if settings is None:
        settings = get_settings()

    pages: list[_PageLines] = []
    band_input: list[tuple[int, list[_Line], float]] = []

    truncated_pages = 0
    with (
        decode_budget(
            settings.pdf_max_stream_bytes, settings.pdf_max_decoded_bytes
        ) as budget,
        pdfplumber.open(io.BytesIO(raw)) as pdf,
    ):
        for index, page in enumerate(pdf.pages):
            if index >= settings.pdf_max_pages:
                truncated_pages = len(pdf.pages) - index
                break
            lines = _group_lines(page)
            band_input.append((page.page_number, lines, float(page.height)))
            tables = page.find_tables()
            page_lines = _PageLines(
                page_number=page.page_number,
                lines=lines,
                tables=[t.extract() for t in tables],
                table_boxes=[_table_box(t) for t in tables],
            )
            pages.append(page_lines)

    chrome = _repeated_chrome(band_input)
    page_heights = {pn: height for pn, _lines, height in band_input}
    for page_lines in pages:
        height = page_heights[page_lines.page_number]
        page_lines.lines = [
            line
            for line in page_lines.lines
            if not (
                _in_band(line, height) is not None and _chrome_key(line.text) in chrome
            )
        ]

    surviving = [line for page in pages for line in page.lines]
    has_tables = any(page.tables for page in pages)
    if not surviving and not has_tables:
        raise ExtractionEmpty(
            "PDF has no extractable text; OCR is out of scope",
            details={"url": source.url},
        )

    body_size, heading_levels = _infer_heading_levels(surviving)
    builder = _Builder(body_size, heading_levels)
    for page_lines in pages:
        builder.consume_page(page_lines)

    removed = strip_invisible_blocks(builder.blocks)
    assign_block_ids(builder.blocks)
    warnings = _finalize_warnings(builder)
    if removed:
        warnings.append(invisible_text_warning(removed))
    warnings.extend(_limit_warnings(settings, truncated_pages, budget))

    main_text_len = sum(
        len(block.text) for block in builder.blocks if block.type != "code"
    )
    if main_text_len < settings.render_min_text_chars:
        warnings.append(
            Warning(
                code="low_text_content",
                message=(
                    f"extracted main text ({main_text_len} chars) is below the "
                    f"{settings.render_min_text_chars}-char threshold"
                ),
                block_id=None,
            )
        )

    metadata = Metadata(
        title=None,
        lang=None,
        published_at=None,
        modified_at=None,
        jsonld=[],
    )
    return Document(
        document_id=make_document_id(source.url),
        version_id=make_version_id(source.content_hash),
        source=source,
        metadata=metadata,
        blocks=builder.blocks,
        warnings=warnings,
    )
