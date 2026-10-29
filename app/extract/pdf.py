"""PDF -> IR extraction (SPEC §4).

``extract_pdf`` turns the raw bytes of a PDF into a
:class:`~app.extract.ir.Document`, page by page, using pdfplumber (MIT; pulls
pdfminer.six MIT/BSD). Everything here is DETERMINISTIC and pure — no LLM, no
network, no OCR, no time limits (CLAUDE.md). The pipeline (SPEC §4 "Extração
PDF", M10b Phase P — DECISIONS.md 2026-10-29):

1. Open the bytes with ``pdfplumber.open`` inside the pdfminer decode budget
   (``pdf_limits``). Pages past ``pdf_max_pages`` are skipped with a warning.
2. WORDS, not raw characters: ``extract_words`` with a gap tolerance relative
   to the font size (``_X_TOLERANCE_RATIO``). pdfTeX does not write space
   characters, so concatenating ``page.chars`` glued every word; the relative
   tolerance splits at the real inter-word gap and expands ligatures.
   Overprinted (fake-bold) duplicate glyphs are removed first.
3. TABLES: pdfplumber's ruled-table finder, kept only when the grid has at
   least two rows and two columns with text (a box around a paragraph, a frame
   around a figure or an empty grid is not a table), plus tables delimited
   only by horizontal rules (booktabs style): rules with the same horizontal
   extent bound a region whose words are split into rows and into columns at
   gaps no word crosses.
4. ROWS: the remaining words are grouped into visual rows by vertical overlap.
   Repeated running headers/footers (a row in the top or bottom 15% band whose
   text, page number stripped, repeats on more than 50% of the pages) are
   dropped.
5. COLUMNS: a page has two columns when a vertical gutter near the middle is
   crossed by a wide gap in many rows. Rows that cross the gutter (titles,
   full-width floats) are read in place; between them, the left column is read
   before the right one.
6. HEADINGS: the two largest font sizes above the body size map to levels 1
   and 2 (SPEC §4). When the PDF has an outline (bookmarks), a row whose text
   matches the next unused outline title (optionally preceded by its section
   number) becomes a heading at the outline's level.
7. PARAGRAPHS: consecutive lines of one column join into one ``paragraph``
   block unless the font size changes, the vertical step exceeds the
   document's usual line pitch, the line is indented (first-line indent) or
   starts a list item, or the previous line ended short (the next line's first
   word would have fit). A hyphen at the end of a line is removed when it
   splits a word (kept when the hyphenated form occurs elsewhere in the
   document and the joined form does not). Runs of monospace lines become one
   ``code`` block with the horizontal spacing rebuilt from glyph positions.
8. Every block carries its 1-based ``page``; ``assign_block_ids`` runs ONCE.
   ``metadata.title`` is the largest text on page 1, else the PDF's /Title.
   Warnings: ``table_no_header``, ``pdf_truncated``, ``low_text_content`` and
   ``pages_without_text`` (pages with images or drawings but no extractable
   text: a scanned page, or text in a font encoding pdfminer cannot decode;
   no OCR, so their content is missing). A PDF with no extractable text at all
   raises :class:`ExtractionEmpty` (HTTP 422).
"""

from __future__ import annotations

import io
import re
import statistics
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from operator import itemgetter
from typing import TYPE_CHECKING, Any

import pdfplumber
from pdfplumber.utils.clustering import cluster_objects

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

# Two glyphs belong to the same word when the gap between them is at most this
# fraction of the font size. pdfplumber's default (3 pt absolute) is wider than
# the inter-word gap of tightly set LaTeX text and glues the words.
_X_TOLERANCE_RATIO = 0.15

# A gap of at least this many font sizes separates table columns or page
# columns; an inter-word space is about 0.25-0.5 of the font size.
_WIDE_GAP_RATIO = 0.75

# The page-column gutter is searched in this central share of the page width.
_GUTTER_BAND = (0.3, 0.7)

#: Warning code when a PDF resource limit truncated the extraction (audit A2).
PDF_TRUNCATED_WARNING_CODE = "pdf_truncated"

#: Warning code for pages that have images or drawings but no extractable text
#: (scanned pages, or text whose font encoding pdfminer cannot decode).
PAGES_WITHOUT_TEXT_WARNING_CODE = "pages_without_text"

_LIST_MARKER_RE = re.compile(
    r"^(?:[•◦▪‣●○■□–—*]|\(?\d{1,3}[.)]|\(?[a-z][.)]|\((?:[ivx]{1,4})\))$"
)
# A section number once punctuation is gone ("2.1" -> "21", "A.3" -> "a3").
_NUMBERING_RE = re.compile(r"\d+|[a-z]\d*")
# pdfminer's placeholder for a glyph with no Unicode mapping: carries nothing.
_CID_RE = re.compile(r"\(cid:\d+\)")
# A table-of-contents entry: a run of leader dots, then a page number.
_LEADER_RE = re.compile(r"(?:\.\s?){3,}\s*[0-9ivxlc]+$", re.IGNORECASE)
_JUNK_TITLE_RE = re.compile(
    r"(?:\.(?:docx?|odt|rtf|tex|dvi|pdf|pptx?|indd|xlsx?)$)|^untitled|^microsoft word",
    re.IGNORECASE,
)


# --------------------------------------------------------------------------- #
# Geometry primitives
# --------------------------------------------------------------------------- #
@dataclass
class _Word:
    """One word: text, bounding box and dominant font."""

    text: str
    x0: float
    x1: float
    top: float
    bottom: float
    size: float
    fontname: str
    baseline: float = 0.0


def _is_monospace(fontname: str) -> bool:
    """Heuristic: a font whose name marks it as monospaced (code-like).

    Deterministic substring match on the PostScript font name (DECISIONS.md
    2026-10-11). Catches the common families used for code in technical PDFs
    (Courier, Mono, Consolas, Menlo, TeX's typewriter ``cmtt``/``sfmono``).
    """
    low = fontname.lower()
    return any(token in low for token in ("courier", "mono", "consol", "menlo", "cmtt"))


def _is_bold(fontname: str) -> bool:
    """Heuristic: a bold face by its font name (``Bold``, TeX ``cmbx``...)."""
    low = fontname.lower()
    return any(t in low for t in ("bold", "black", "heavy", "demi")) or bool(
        re.search(r"bx\d", low)
    )


@dataclass
class _Line:
    """A run of words read as one visual line of one column."""

    words: list[_Word]
    page_number: int
    column: int = 0  # 0 = full width, 1 = left column, 2 = right column

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)

    @property
    def x0(self) -> float:
        return min(w.x0 for w in self.words)

    @property
    def x1(self) -> float:
        return max(w.x1 for w in self.words)

    @property
    def top(self) -> float:
        return min(w.top for w in self.words)

    @property
    def bottom(self) -> float:
        return max(w.bottom for w in self.words)

    @property
    def baseline(self) -> float:
        """Baseline of the line's largest word (its main text line)."""
        return max(self.words, key=lambda w: (w.size, -w.x0)).baseline

    def _dominant(self) -> _Word:
        """The word carrying most characters at the most common size/font."""
        weights: Counter[tuple[float, str]] = Counter()
        for w in self.words:
            weights[(w.size, w.fontname)] += len(w.text)
        (size, font), _ = min(weights.items(), key=lambda kv: (-kv[1], kv[0]))
        return _Word("", 0, 0, 0, 0, size, font)

    @property
    def size(self) -> float:
        return self._dominant().size

    @property
    def monospace(self) -> bool:
        return _is_monospace(self._dominant().fontname)

    @property
    def bold(self) -> bool:
        return _is_bold(self._dominant().fontname)


@dataclass
class _Table:
    """A detected table: its bounding box and cell grid."""

    x0: float
    x1: float
    top: float
    bottom: float
    rows: list[list[str]]
    header_rows: int | None = None  # rows above a mid rule, when known


@dataclass
class _Item:
    """One reading-order element of a page: a line or a table."""

    page_number: int
    column: int
    line: _Line | None = None
    table: _Table | None = None


@dataclass
class _Page:
    """Per-page intermediate state."""

    page_number: int
    width: float
    height: float
    rows: list[_Line] = field(default_factory=list)
    tables: list[_Table] = field(default_factory=list)
    items: list[_Item] = field(default_factory=list)


def _round_size(size: float) -> float:
    """Round a font size to one decimal so near-equal sizes group together."""
    return round(size, 1)


# --------------------------------------------------------------------------- #
# Words and rows
# --------------------------------------------------------------------------- #
def _unique_page(page: Any) -> Any:
    """The page without overprinted duplicate glyphs (fake bold, shadows).

    Same rule as pdfplumber's ``dedupe_chars`` (same upright flag, text, font
    and size, positions within 1 pt: the first glyph in position order is
    kept), but linear: pdfplumber restores the original order with
    ``sorted(key=list.index)``, which is quadratic in the glyphs of a page and
    tripled the extraction time of long technical PDFs.
    """
    chars = page.chars
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for char in chars:
        key = (char["upright"], char["text"], char["fontname"], char["size"])
        groups.setdefault(key, []).append(char)
    drop: set[int] = set()
    for grp in groups.values():
        if len(grp) < 2:
            continue
        for y_cluster in cluster_objects(grp, itemgetter("doctop"), 1):
            if len(y_cluster) < 2:
                continue
            for x_cluster in cluster_objects(y_cluster, itemgetter("x0"), 1):
                keep = min(x_cluster, key=itemgetter("doctop", "x0"))
                drop.update(id(c) for c in x_cluster if c is not keep)
    if not drop:
        return page
    return page.filter(lambda obj: id(obj) not in drop)


def _page_words(page: Any) -> list[_Word]:
    """Extract the page's upright words with their dominant size and font."""
    raw = _unique_page(page).extract_words(
        x_tolerance_ratio=_X_TOLERANCE_RATIO,
        y_tolerance=3,
        keep_blank_chars=False,
        use_text_flow=False,
        return_chars=True,
    )
    height = float(page.height)
    words: list[_Word] = []
    for item in raw:
        text = _CID_RE.sub("", str(item.get("text", "")))
        if not text.strip() or not item.get("upright", True):
            continue
        chars = item.get("chars") or []
        sizes: Counter[float] = Counter(
            _round_size(float(c.get("size", 0.0))) for c in chars
        )
        fonts: Counter[str] = Counter(str(c.get("fontname", "")) for c in chars)
        size = min(sizes.items(), key=lambda kv: (-kv[1], kv[0]))[0] if sizes else 0.0
        font = min(fonts.items(), key=lambda kv: (-kv[1], kv[0]))[0] if fonts else ""
        bottom = float(item["bottom"])
        matrix = chars[0].get("matrix") if chars else None
        baseline = (
            height - float(matrix[5])
            if isinstance(matrix, (tuple, list)) and len(matrix) == 6
            else bottom
        )
        words.append(
            _Word(
                text=text,
                x0=float(item["x0"]),
                x1=float(item["x1"]),
                top=float(item["top"]),
                bottom=bottom,
                size=size,
                fontname=font,
                baseline=baseline,
            )
        )
    return words


def _group_rows(words: Iterable[_Word], page_number: int) -> list[_Line]:
    """Group words into visual rows by baseline, top to bottom.

    A word joins the current row when its baseline is within half a font size
    of the row's anchor baseline (the baseline of the row's largest word), so
    superscripts and subscripts stay on their line while consecutive lines
    (about 1.2 font sizes apart) stay apart. Baselines, not glyph boxes: some
    math fonts declare boxes that sit below the baseline. Words in a row are
    ordered by ``x0``.
    """
    ordered = sorted(words, key=lambda w: (w.baseline, w.x0, w.text))
    rows: list[list[_Word]] = []
    anchor: _Word | None = None
    for word in ordered:
        if anchor is not None and abs(word.baseline - anchor.baseline) <= 0.5 * max(
            anchor.size, word.size
        ):
            rows[-1].append(word)
            if word.size > anchor.size:
                anchor = word
            continue
        rows.append([word])
        anchor = word
    return [
        _Line(words=sorted(r, key=lambda w: (w.x0, w.top)), page_number=page_number)
        for r in rows
    ]


# --------------------------------------------------------------------------- #
# Repeated header/footer removal (SPEC §4)
# --------------------------------------------------------------------------- #
def _in_band(line: _Line, page_height: float) -> str | None:
    """Return 'top'/'bottom' if the line sits in a header/footer band, else None."""
    band = page_height * _BAND_FRACTION
    if line.top <= band:
        return "top"
    if line.bottom >= page_height - band:
        return "bottom"
    return None


_PAGE_NUMBER_RE = re.compile(r"(?:^\s*\d+\s+)|(?:\s+\d+\s*$)|(?:^\s*\d+\s*$)")
# A band line that is only a page number (arabic or roman): page numbers vary,
# so they all share one chrome key and are dropped when most pages carry one.
_BARE_PAGE_NUMBER_RE = re.compile(r"\d{1,4}|[ivxlc]{1,7}", re.IGNORECASE)


def _chrome_key(text: str) -> str:
    """Normalize a band line for repeated-chrome comparison (SPEC §4).

    Running footers routinely carry a VARYING page number ("Page 1", "... 2"),
    so a raw text comparison would never see them as repeated. The page-number
    token (a standalone run of digits at the start or end of the line) is dropped
    before comparison so the stable chrome text matches across pages (DECISIONS
    2026-10-11). Deterministic and conservative: only leading/trailing bare
    integers are stripped, never digits embedded in words. A line that is only a
    page number gets one shared key, so bare page numbers count as repeated.
    """
    text = normalize_text(text)
    if _BARE_PAGE_NUMBER_RE.fullmatch(text):
        return "#page-number"
    return normalize_text(_PAGE_NUMBER_RE.sub(" ", text))


def _repeated_chrome(pages: list[_Page]) -> set[str]:
    """Return chrome keys that repeat as header/footer on >50% of pages.

    Counting is per page (a row repeated twice on one page counts once), so the
    threshold reflects how many PAGES share the chrome.
    """
    total_pages = len(pages)
    if total_pages <= 1:
        return set()
    pages_with_text: Counter[str] = Counter()
    for page in pages:
        seen: set[str] = set()
        for row in page.rows:
            if _in_band(row, page.height) is None:
                continue
            key = _chrome_key(row.text)
            if key:
                seen.add(key)
        pages_with_text.update(seen)
    threshold = total_pages * _REPEAT_THRESHOLD
    return {key for key, count in pages_with_text.items() if count > threshold}


# --------------------------------------------------------------------------- #
# Tables
# --------------------------------------------------------------------------- #
def _clean_grid(rows: list[list[str | None]]) -> list[list[str]]:
    grid = [[normalize_text(cell or "") for cell in row] for row in rows]
    return [row for row in grid if any(row)]


def _is_real_grid(grid: list[list[str]]) -> bool:
    """A table needs two rows and two columns that carry text."""
    if len(grid) < 2:
        return False
    width = max(len(row) for row in grid)
    filled_columns = sum(
        1 for col in range(width) if any(col < len(r) and r[col] for r in grid)
    )
    return filled_columns >= 2


def _ruled_tables(page: Any) -> list[_Table]:
    """Tables from pdfplumber's ruling-line finder, spurious grids dropped."""
    tables: list[_Table] = []
    for found in page.find_tables():
        grid = _clean_grid(found.extract())
        if not _is_real_grid(grid):
            continue
        x0, top, x1, bottom = (float(v) for v in found.bbox)
        tables.append(_Table(x0=x0, x1=x1, top=top, bottom=bottom, rows=grid))
    return tables


def _horizontal_rules(page: Any) -> list[tuple[float, float, float]]:
    """Thin horizontal segments of the page as (top, x0, x1)."""
    rules: list[tuple[float, float, float]] = []
    for obj in list(page.lines) + list(page.rects):
        x0, x1 = float(obj["x0"]), float(obj["x1"])
        top, bottom = float(obj["top"]), float(obj["bottom"])
        if bottom - top <= 1.5 and x1 - x0 >= 40:
            rules.append((round(top, 1), round(x0, 1), round(x1, 1)))
    return sorted(set(rules))


def _split_columns(rows: list[_Line]) -> list[tuple[float, float]]:
    """Column extents: x ranges covered by words, split at wide gaps."""
    spans = sorted((w.x0, w.x1, w.size) for r in rows for w in r.words)
    columns: list[list[float]] = []
    for x0, x1, size in spans:
        if columns and x0 - columns[-1][1] < _WIDE_GAP_RATIO * size:
            columns[-1][1] = max(columns[-1][1], x1)
        else:
            columns.append([x0, x1])
    return [(c[0], c[1]) for c in columns]


def _grid_from_rows(
    rows: list[_Line], columns: list[tuple[float, float]]
) -> list[list[str]]:
    grid: list[list[str]] = []
    for row in rows:
        cells = [""] * len(columns)
        for word in row.words:
            center = (word.x0 + word.x1) / 2.0
            idx = next(
                (i for i, (c0, c1) in enumerate(columns) if c0 - 1 <= center <= c1 + 1),
                len(columns) - 1,
            )
            cells[idx] = f"{cells[idx]} {word.text}".strip()
        grid.append(cells)
    return grid


def _rule_tables(page: Any, words: list[_Word], taken: list[_Table]) -> list[_Table]:
    """Tables bounded only by horizontal rules of equal extent (booktabs).

    Rules sharing the same left and right ends (within 2 pt) are grouped; the
    region from the first to the last rule of a group (when no further apart
    than 40% of the page height) is a table candidate. Its words are grouped
    into rows and split into columns at gaps that no word crosses; the
    candidate is kept only with two or more columns, two or more body rows and
    most rows filling more than one cell. Rows above the second rule are the
    header when there are three or more rules.
    """
    rules = _horizontal_rules(page)
    groups: list[list[tuple[float, float, float]]] = []
    for rule in sorted(rules, key=lambda r: (r[1], r[2], r[0])):
        for group in groups:
            if abs(group[0][1] - rule[1]) <= 2 and abs(group[0][2] - rule[2]) <= 2:
                group.append(rule)
                break
        else:
            groups.append([rule])

    tables: list[_Table] = []
    max_span = 0.4 * float(page.height)
    for group in groups:
        tops = sorted({r[0] for r in group})
        # Collapse double rules drawn a hair apart.
        merged: list[float] = []
        for t in tops:
            if not merged or t - merged[-1] > 2:
                merged.append(t)
        if len(merged) < 2:
            continue
        x0 = min(r[1] for r in group)
        x1 = max(r[2] for r in group)
        # Split the rule sequence where two rules are too far apart.
        runs: list[list[float]] = [[merged[0]]]
        for t in merged[1:]:
            if t - runs[-1][-1] > max_span:
                runs.append([t])
            else:
                runs[-1].append(t)
        for run in runs:
            if len(run) < 2:
                continue
            top, bottom = run[0], run[-1]
            if any(
                t.top <= bottom and top <= t.bottom and t.x0 <= x1 and x0 <= t.x1
                for t in [*taken, *tables]
            ):
                continue
            inside = [
                w
                for w in words
                if w.top >= top - 1
                and w.bottom <= bottom + 1
                and w.x0 >= x0 - 2
                and w.x1 <= x1 + 2
            ]
            table = _table_from_words(inside, run, x0, x1)
            if table is not None:
                tables.append(table)
    return tables


def _table_from_words(
    words: list[_Word], rules: list[float], x0: float, x1: float
) -> _Table | None:
    rows = _group_rows(words, 0)
    if len(rows) < 3:
        return None
    columns = _split_columns(rows)
    if len(columns) < 2:
        return None
    grid = _grid_from_rows(rows, columns)
    multi = sum(1 for r in grid if sum(1 for c in r if c) >= 2)
    if multi * 2 < len(grid):
        return None
    header_rows: int | None = None
    if len(rules) >= 3:
        mid = rules[1]
        header_rows = sum(1 for r in rows if r.bottom <= mid + 1)
        if header_rows == 0 or header_rows >= len(rows):
            header_rows = None
    if header_rows and header_rows > 1:
        joined = [
            " ".join(grid[i][c] for i in range(header_rows) if grid[i][c])
            for c in range(len(columns))
        ]
        grid = [joined, *grid[header_rows:]]
        header_rows = 1
    if len(grid) - (header_rows or 0) < 2:
        return None
    return _Table(
        x0=x0,
        x1=x1,
        top=rules[0],
        bottom=rules[-1],
        rows=grid,
        header_rows=header_rows,
    )


def _build_table_data(table: _Table) -> tuple[dict[str, Any], bool]:
    """Build the SPEC §4 table ``data`` and report whether a header was found.

    The header is the block of rows above a booktabs mid rule when known;
    otherwise the first row is treated as the header when it is fully populated
    (every cell has text) and there is at least one body row. Without a header
    the headers are empty and the caller emits a ``table_no_header`` warning.
    """
    grid = table.rows
    headers: list[str] = []
    body = grid
    if table.header_rows == 1 and len(grid) >= 2:
        headers, body = grid[0], grid[1:]
    elif len(grid) >= 2 and all(cell for cell in grid[0]):
        headers, body = grid[0], grid[1:]
    data: dict[str, Any] = {
        "caption": None,
        "headers": headers,
        "rows": body,
        "notes": [],
    }
    return data, bool(headers)


def _word_in_table(word: _Word, tables: list[_Table]) -> bool:
    """A word belongs to a table when it overlaps the table's box horizontally
    and its vertical center is inside it (a cell's overflowing word included,
    so its tail never leaks out as a stray paragraph)."""
    cy = (word.top + word.bottom) / 2.0
    return any(
        word.x0 < t.x1 + 1 and t.x0 - 1 < word.x1 and t.top - 1 <= cy <= t.bottom + 1
        for t in tables
    )


# --------------------------------------------------------------------------- #
# Columns and reading order
# --------------------------------------------------------------------------- #
def _find_gutter(rows: list[_Line], width: float) -> float | None:
    """Return the x of a two-column gutter, or None for a single column.

    For every x in the central band of the page, count the rows where x falls
    in a WIDE gap (>= 0.75 font sizes) between two words, and the rows where a
    word covers x. The gutter is the x with the most wide-gap rows; it is
    accepted when those rows are at least 4 and 30% of the rows with text on
    both halves of the page, outnumber the rows crossing it, and both sides
    carry at least 4 rows. Monospace words are ignored (aligned code).
    """
    lo, hi = int(width * _GUTTER_BAND[0]), int(width * _GUTTER_BAND[1])
    if hi <= lo:
        return None
    span = hi - lo + 1
    wide = [0] * (span + 1)
    cross = [0] * (span + 1)
    left_rows = right_rows = 0

    def _add(arr: list[int], a: float, b: float) -> None:
        start = max(lo, int(a) + 1)
        end = min(hi, int(b))
        if start <= end:
            arr[start - lo] += 1
            arr[end - lo + 1] -= 1

    candidate_rows = 0
    for row in rows:
        words = [w for w in row.words if not _is_monospace(w.fontname)]
        if not words:
            continue
        if any(w.x0 < width / 2 for w in words):
            left_rows += 1
        if any(w.x1 > width / 2 for w in words):
            right_rows += 1
        if words[0].x0 < lo and words[-1].x1 > hi:
            candidate_rows += 1
        for w in words:
            _add(cross, w.x0, w.x1)
        for a, b in zip(words, words[1:], strict=False):
            if b.x0 - a.x1 >= _WIDE_GAP_RATIO * max(a.size, b.size):
                _add(wide, a.x1, b.x0)
    if left_rows < 4 or right_rows < 4:
        return None

    best_x, best_wide, best_cross = None, 0, 0
    run_wide = run_cross = 0
    center = width / 2.0
    for i in range(span):
        run_wide += wide[i]
        run_cross += cross[i]
        x = lo + i
        better = run_wide > best_wide or (
            run_wide == best_wide
            and best_x is not None
            and abs(x - center) < abs(best_x - center)
        )
        if run_wide > 0 and better:
            best_x, best_wide, best_cross = x, run_wide, run_cross
    if best_x is None:
        return None
    if best_wide < 4 or best_wide < 0.3 * max(candidate_rows, 1):
        return None
    if best_cross >= best_wide:
        return None
    if not _columns_aligned(rows, float(best_x)):
        return None
    return float(best_x)


def _cluster_share(values: list[float], tolerance: float = 3.0) -> float:
    """Largest share of values within ``tolerance`` of one of them."""
    if not values:
        return 0.0
    best = max(sum(1 for v in values if abs(v - c) <= tolerance) for c in values)
    return best / len(values)


def _columns_aligned(rows: list[_Line], gutter: float) -> bool:
    """Both sides of the gutter read as text columns with aligned left edges.

    Diagrams, field layouts and aligned lists also leave a vertical gap; real
    columns differ in that most rows have text on both sides, and both the
    left parts and the right parts start at a common left edge.
    """
    text_rows = 0
    lefts: list[float] = []
    rights: list[float] = []
    for row in rows:
        words = [w for w in row.words if not _is_monospace(w.fontname)]
        if not words:
            continue
        text_rows += 1
        lw = [w for w in words if w.x1 <= gutter]
        rw = [w for w in words if w.x0 >= gutter]
        if lw and rw and len(lw) + len(rw) == len(words):
            lefts.append(lw[0].x0)
            rights.append(rw[0].x0)
    split = len(lefts)
    if split < max(5, 0.4 * text_rows):
        return False
    return _cluster_share(lefts) >= 0.6 and _cluster_share(rights) >= 0.6


def _order_page(page: _Page) -> list[_Item]:
    """Reading-order items of a page: lines split by column, and tables."""
    gutter = _find_gutter(page.rows, page.width)
    entries: list[tuple[float, int, _Line | _Table]] = [
        (row.top, 0, row) for row in page.rows
    ] + [(t.top, 1, t) for t in page.tables]
    entries.sort(key=lambda e: (e[0], e[1]))

    items: list[_Item] = []
    left: list[_Item] = []
    right: list[_Item] = []

    def _flush() -> None:
        items.extend(left)
        items.extend(right)
        left.clear()
        right.clear()

    pn = page.page_number
    for _top, _kind, entry in entries:
        if isinstance(entry, _Table):
            if gutter is None or (entry.x0 < gutter < entry.x1):
                _flush()
                items.append(_Item(pn, 0, table=entry))
            elif entry.x1 <= gutter:
                left.append(_Item(pn, 1, table=entry))
            else:
                right.append(_Item(pn, 2, table=entry))
            continue
        row = entry
        if gutter is None:
            items.append(_Item(pn, 0, line=row))
            continue
        lw = [w for w in row.words if w.x1 <= gutter]
        rw = [w for w in row.words if w.x0 >= gutter]
        crossing = len(lw) + len(rw) < len(row.words)
        narrow = (
            bool(lw)
            and bool(rw)
            and rw[0].x0 - lw[-1].x1 < _WIDE_GAP_RATIO * max(lw[-1].size, rw[0].size)
        )
        if crossing or narrow:
            _flush()
            items.append(_Item(pn, 0, line=row))
            continue
        if lw:
            left.append(_Item(pn, 1, line=_Line(lw, pn, 1)))
        if rw:
            right.append(_Item(pn, 2, line=_Line(rw, pn, 2)))
    _flush()
    return items


# --------------------------------------------------------------------------- #
# Headings: font size (SPEC §4) and the PDF outline
# --------------------------------------------------------------------------- #
def _body_size(lines: list[_Line]) -> float:
    """The most common rounded font size across lines (ties: the smaller)."""
    size_counts: Counter[float] = Counter(line.size for line in lines)
    if not size_counts:
        return 0.0
    max_count = max(size_counts.values())
    return min(size for size, count in size_counts.items() if count == max_count)


def _heading_sizes(lines: list[_Line], body_size: float) -> list[float]:
    """Font sizes that mark headings, largest first (SPEC §4).

    Every size at least half a point above the body size, used on lines that
    carry some letters, and on fewer lines than half the body lines (a second
    body font is not a heading font).
    """
    counts: Counter[float] = Counter()
    body_lines = 0
    for line in lines:
        if line.size == body_size:
            body_lines += 1
        elif line.size >= body_size + 0.5 and _is_heading_text(line):
            counts[line.size] += 1
    return sorted(
        (size for size, n in counts.items() if n <= max(1, body_lines // 2)),
        reverse=True,
    )


def _is_heading_text(line: _Line) -> bool:
    text = line.text
    return (
        not line.monospace
        and len(text) <= 200
        and sum(ch.isalpha() for ch in text) >= 2
    )


def _classify_headings(
    items: list[_Item], outline: list[tuple[int, str]]
) -> tuple[float, dict[int, tuple[int, int]]]:
    """Body size and {item index: (heading level, lines used)}.

    Outline first: a line set larger than the body or in bold whose text (or
    whose text joined with the next line's) matches the next unused outline
    title, optionally after its section number, is a heading at the outline's
    level. Then font size: every heading size (``_heading_sizes``) gets a
    level. Without an outline, sizes rank 1, 2, 3... largest first (capped at
    6). With an outline, a size takes the most common outline level of the
    lines matched at that size; an unmatched size takes one level below the
    nearest larger size, or 1 when it is the largest.
    """
    lines = [(i, it.line) for i, it in enumerate(items) if it.line is not None]
    body_size = _body_size([line for _, line in lines])
    matcher = _OutlineMatcher(outline)
    found: dict[int, tuple[int, int]] = {}
    matched_levels: dict[float, list[int]] = {}
    if matcher:
        pos = 0
        while pos < len(lines):
            idx, line = lines[pos]
            if not _is_heading_text(line) or not (
                line.size >= body_size + 0.5 or line.bold
            ):
                pos += 1
                continue
            level = matcher.match(line.text)
            used = 1
            if level is None and pos + 1 < len(lines):
                nidx, nline = lines[pos + 1]
                if (
                    nidx == idx + 1
                    and nline.size == line.size
                    and nline.page_number == line.page_number
                ):
                    level = matcher.match(f"{line.text} {nline.text}")
                    used = 2
            if level is None:
                pos += 1
                continue
            found[idx] = (level, used)
            matched_levels.setdefault(line.size, []).append(level)
            pos += used

    sizes = _heading_sizes([line for _, line in lines], body_size)
    size_level: dict[float, int] = {}
    for rank, size in enumerate(sizes, start=1):
        if not matched_levels:
            size_level[size] = min(rank, 6)
        elif size in matched_levels:
            votes = Counter(matched_levels[size])
            size_level[size] = min(votes.items(), key=lambda kv: (-kv[1], kv[0]))[0]
        else:
            larger = [size_level[s] for s in sizes if s > size and s in size_level]
            size_level[size] = min(larger[-1] + 1, 6) if larger else 1
    covered: set[int] = set()
    for idx, (_level, used) in found.items():
        covered.update(range(idx, idx + used))
    for idx, line in lines:
        if idx in covered or not _is_heading_text(line):
            continue
        level = size_level.get(line.size)
        if level is not None:
            found[idx] = (level, 1)
    return body_size, found


def _heading_key(text: str) -> str:
    """Letters and digits only, case-folded: quotes, dashes and dots vary."""
    return re.sub(r"[\W_]+", "", text.casefold())


def _read_outline(pdf: Any) -> list[tuple[int, str]]:
    """The PDF outline as (level, title); empty when absent or unreadable."""
    try:
        entries = list(pdf.doc.get_outlines())
    except Exception:  # noqa: BLE001 - absent or malformed outline: no outline
        return []
    outline: list[tuple[int, str]] = []
    for entry in entries:
        level, title = entry[0], entry[1]
        if not isinstance(title, str) or not isinstance(level, int):
            continue
        key = _heading_key(title)
        if len(key) >= 2:
            outline.append((max(1, min(level, 6)), title))
    return outline


class _OutlineMatcher:
    """Match rows to unused outline titles, in document order."""

    def __init__(self, outline: list[tuple[int, str]]) -> None:
        self._entries = [(level, _heading_key(title)) for level, title in outline]
        self._used = [False] * len(self._entries)

    def __bool__(self) -> bool:
        return bool(self._entries)

    @staticmethod
    def _matches(line_key: str, title_key: str) -> bool:
        if line_key == title_key:
            return True
        if line_key.endswith(title_key):
            return bool(_NUMBERING_RE.fullmatch(line_key[: -len(title_key)]))
        if title_key.endswith(line_key) and len(line_key) >= 4:
            return bool(_NUMBERING_RE.fullmatch(title_key[: -len(line_key)]))
        return False

    def match(self, text: str) -> int | None:
        """Return the outline level for ``text`` and consume the entry."""
        key = _heading_key(text)
        if len(key) < 2:
            return None
        for i, (level, title_key) in enumerate(self._entries):
            if not self._used[i] and self._matches(key, title_key):
                self._used[i] = True
                return level
        return None


# --------------------------------------------------------------------------- #
# Paragraph assembly
# --------------------------------------------------------------------------- #
def _line_pitches(items: list[_Item]) -> dict[float, float]:
    """Median top-to-top step between consecutive lines, per font size."""
    steps: dict[float, list[float]] = {}
    prev: _Line | None = None
    prev_key: tuple[int, int] | None = None
    for item in items:
        line = item.line
        if line is None:
            prev = None
            continue
        key = (item.page_number, item.column)
        if prev is not None and prev_key == key and prev.size == line.size:
            step = line.baseline - prev.baseline
            if 0.8 * line.size <= step <= 3.0 * line.size:
                steps.setdefault(line.size, []).append(step)
        prev, prev_key = line, key
    return {size: statistics.median(vals) for size, vals in steps.items() if vals}


_EdgeKey = tuple[int, int, float]


def _column_edges(items: list[_Item]) -> dict[_EdgeKey, tuple[float, float]]:
    """Left and right text edge of every (page, column, font size).

    Per font size, so that a block set smaller and inset (a note, a comment)
    gets its own edges instead of looking indented and short.
    """
    xs: dict[_EdgeKey, tuple[list[float], list[float]]] = {}
    for item in items:
        if item.line is None or len(item.line.words) < 3:
            continue
        key = (item.page_number, item.column, item.line.size)
        lefts, rights = xs.setdefault(key, ([], []))
        lefts.append(item.line.x0)
        rights.append(item.line.x1)
    edges: dict[_EdgeKey, tuple[float, float]] = {}
    for key, (lefts, rights) in xs.items():
        lefts.sort()
        rights.sort()
        edges[key] = (lefts[len(lefts) // 10], rights[(len(rights) * 9) // 10])
    return edges


def _vocabulary(items: list[_Item]) -> Counter[str]:
    """Lower-cased words of the document (for line-end hyphen decisions)."""
    vocab: Counter[str] = Counter()
    for item in items:
        if item.line is None:
            continue
        for word in item.line.words[:-1]:
            token = word.text.strip(".,;:!?()[]\"'").lower()
            if token:
                vocab[token] += 1
    return vocab


_HYPHENS = ("-", "­", "‐")


def _join_lines(prev_text: str, next_text: str, vocab: Counter[str]) -> str:
    """Join two lines of a paragraph, undoing a line-end hyphenation."""
    if prev_text.endswith("­"):
        return prev_text[:-1] + next_text
    if (
        prev_text.endswith(_HYPHENS)
        and len(prev_text) >= 2
        and prev_text[-2].isalpha()
        and next_text[:1].islower()
    ):
        head = prev_text[:-1].rsplit(" ", 1)[-1]
        tail = next_text.split(" ", 1)[0]
        tail_core = tail.rstrip(".,;:!?)]\"'")
        hyphenated = f"{head}-{tail_core}".lower().lstrip("([\"'")
        joined = f"{head}{tail_core}".lower().lstrip("([\"'")
        keep = "-" in head or (vocab[hyphenated] > 0 and vocab[joined] == 0)
        if keep:
            return prev_text[:-1] + "-" + next_text
        return prev_text[:-1] + next_text
    return f"{prev_text} {next_text}"


class _Builder:
    """Deterministic builder turning reading-order items into IR blocks."""

    def __init__(
        self, items: list[_Item], headings: dict[int, tuple[int, int]]
    ) -> None:
        self._items = items
        self._headings = headings
        self._pitch = _line_pitches(items)
        self._edges = _column_edges(items)
        self._vocab = _vocabulary(items)
        self.blocks: list[Block] = []
        self.warnings: list[Warning] = []
        self._heading_stack: list[tuple[int, str]] = []
        self._order = 0

    # -- emission ----------------------------------------------------------- #
    def _emit(
        self,
        block_type: str,
        *,
        text: str,
        data: dict[str, Any],
        page: int,
        level: int | None = None,
    ) -> None:
        self.blocks.append(
            Block(
                block_id="",  # assigned later by assign_block_ids
                type=block_type,  # type: ignore[arg-type]
                order=self._order,
                level=level,
                heading_path=[title for _, title in self._heading_stack],
                text=text,
                data=data,
                links=[],
                page=page,
            )
        )
        self._order += 1

    def _emit_heading(self, text: str, level: int, page: int) -> None:
        text = normalize_text(text)
        if not text:
            return
        while self._heading_stack and self._heading_stack[-1][0] >= level:
            self._heading_stack.pop()
        self._emit("heading", text=text, data={}, page=page, level=level)
        self._heading_stack.append((level, text))

    def _emit_table(self, table: _Table, page: int) -> None:
        data, has_header = _build_table_data(table)
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

    def _emit_code(self, lines: list[_Line]) -> None:
        code = _code_text(lines)
        if code.strip():
            self._emit(
                "code",
                text=code,
                data={"language": None, "code": code},
                page=lines[0].page_number,
            )

    def _emit_paragraph(self, lines: list[_Line]) -> None:
        text = lines[0].text
        for line in lines[1:]:
            text = _join_lines(text, line.text, self._vocab)
        text = normalize_text(text)
        if text:
            self._emit("paragraph", text=text, data={}, page=lines[0].page_number)

    # -- classification ---------------------------------------------------- #
    def _breaks(
        self, prev: _Line, prev_item: _Item, cur: _Line, cur_item: _Item
    ) -> bool:
        """True when ``cur`` starts a new paragraph after ``prev``."""
        if cur_item.page_number != prev_item.page_number:
            return True
        if abs(cur.size - prev.size) > 0.6:
            return True
        size = max(cur.size, 1.0)
        first = cur.words[0]
        if _LIST_MARKER_RE.match(first.text):
            return True
        _p_left, p_right = self._edges.get(
            (prev_item.page_number, prev_item.column, prev.size), (prev.x0, prev.x1)
        )
        c_left, _c_right = self._edges.get(
            (cur_item.page_number, cur_item.column, cur.size), (cur.x0, cur.x1)
        )
        if _LEADER_RE.search(prev.text):
            return True
        # The previous line ended short: the next line's first word would have
        # fit after it, so the break was deliberate.
        if p_right - prev.x1 > (first.x1 - first.x0) + 0.5 * size:
            return True
        indented = cur.x0 - c_left > 0.5 * size
        if cur_item.column != prev_item.column:
            # Column change on the same page: continue only an unfinished
            # paragraph into a non-indented first line.
            return cur_item.column == 0 or prev_item.column == 0 or indented
        step = cur.baseline - prev.baseline
        pitch = self._pitch.get(cur.size, 1.2 * size)
        if step <= 0 or step > 1.35 * pitch:
            return True
        hanging = _LIST_MARKER_RE.match(prev.words[0].text) is not None
        return indented and cur.x0 - prev.x0 > 0.5 * size and not hanging

    # -- main loop ----------------------------------------------------------- #
    def build(self) -> None:
        items = self._items
        para: list[_Line] = []
        para_item: _Item | None = None
        code: list[_Line] = []
        code_item: _Item | None = None

        def flush_para() -> None:
            nonlocal para, para_item
            if para:
                self._emit_paragraph(para)
            para, para_item = [], None

        def flush_code() -> None:
            nonlocal code, code_item
            if code:
                self._emit_code(code)
            code, code_item = [], None

        i = 0
        while i < len(items):
            item = items[i]
            if item.table is not None:
                flush_para()
                flush_code()
                self._emit_table(item.table, item.page_number)
                i += 1
                continue
            line = item.line
            assert line is not None
            heading = self._headings.get(i)
            if heading is not None:
                flush_para()
                flush_code()
                level, used = heading
                text = " ".join(
                    it.line.text for it in items[i : i + used] if it.line is not None
                )
                self._emit_heading(text, level, line.page_number)
                i += used
                continue
            if line.monospace:
                flush_para()
                if code_item is not None and (
                    code_item.page_number != item.page_number
                    or code_item.column != item.column
                ):
                    flush_code()
                code.append(line)
                code_item = item
                i += 1
                continue
            flush_code()
            if (
                para
                and para_item is not None
                and self._breaks(para[-1], para_item, line, item)
            ):
                flush_para()
            para.append(line)
            para_item = item
            i += 1
        flush_para()
        flush_code()


def _code_text(lines: list[_Line]) -> str:
    """Rebuild a code block's text, spacing included, from glyph positions."""
    widths = [
        (w.x1 - w.x0) / len(w.text) for line in lines for w in line.words if w.text
    ]
    char_w = statistics.median(widths) if widths else 1.0
    char_w = max(char_w, 0.1)
    left = min(line.x0 for line in lines)
    out: list[str] = []
    for line in lines:
        parts: list[str] = [" " * max(0, round((line.x0 - left) / char_w))]
        prev: _Word | None = None
        for word in line.words:
            if prev is not None:
                parts.append(" " * max(1, round((word.x0 - prev.x1) / char_w)))
            parts.append(word.text)
            prev = word
        out.append("".join(parts).rstrip())
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# Warnings and metadata
# --------------------------------------------------------------------------- #
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


def _page_ranges(pages: list[int]) -> str:
    """Compact "1, 3-5, 9" form of a sorted page list."""
    parts: list[str] = []
    start = prev = pages[0]
    for p in [*pages[1:], None]:
        if p is not None and p == prev + 1:
            prev = p
            continue
        parts.append(str(start) if start == prev else f"{start}-{prev}")
        if p is not None:
            start = prev = p
    return ", ".join(parts)


def _textless_warning(pages: list[int]) -> Warning:
    shown = _page_ranges(pages)
    return Warning(
        code=PAGES_WITHOUT_TEXT_WARNING_CODE,
        message=(
            f"{len(pages)} page(s) have images or drawings but no extractable "
            f"text (pages {shown}): scanned pages or an undecodable font "
            "encoding; OCR is out of scope, so their content is missing"
        ),
        block_id=None,
    )


def _pdf_info_title(pdf: Any) -> str | None:
    raw = pdf.metadata.get("Title") if isinstance(pdf.metadata, dict) else None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    if not isinstance(raw, str):
        return None
    title = normalize_text(raw)
    if len(title) < 3 or _JUNK_TITLE_RE.search(title):
        return None
    return title


def _first_page_title(items: list[_Item], body_size: float) -> str | None:
    """The largest text of page 1 (consecutive lines of that size joined)."""
    first = [it.line for it in items if it.line is not None and it.page_number == 1]
    if not first:
        return None
    biggest = max(line.size for line in first)
    if biggest < 1.2 * body_size:
        return None
    run: list[str] = []
    for line in first:
        if line.size == biggest:
            run.append(line.text)
        elif run:
            break
    title = normalize_text(" ".join(run))
    return title if 3 <= len(title) <= 300 else None


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def extract_pdf(
    raw: bytes, source: SourceInfo, *, settings: Settings | None = None
) -> Document:
    """Extract a :class:`Document` from the raw bytes of a PDF (SPEC §4).

    ``raw`` is the fetched PDF body; ``source`` supplies ``url`` (document id),
    ``content_hash`` (version id) and is expected to carry ``render_mode='pdf'``.
    Extraction is deterministic and page-ordered; every block carries its 1-based
    ``page``.

    Raises :class:`~app.fetch.errors.ExtractionEmpty` (HTTP 422) when the PDF has
    no extractable text at all (e.g. a scanned/image-only PDF). OCR is out of
    scope.
    """
    if settings is None:
        settings = get_settings()

    pages: list[_Page] = []
    textless: list[int] = []
    truncated_pages = 0
    with (
        decode_budget(
            settings.pdf_max_stream_bytes, settings.pdf_max_decoded_bytes
        ) as budget,
        pdfplumber.open(io.BytesIO(raw)) as pdf,
    ):
        info_title = _pdf_info_title(pdf)
        outline = _read_outline(pdf)
        for index, page in enumerate(pdf.pages):
            if index >= settings.pdf_max_pages:
                truncated_pages = len(pdf.pages) - index
                break
            words = _page_words(page)
            if not words and (page.images or page.curves or page.rects):
                textless.append(page.page_number)
            tables = _ruled_tables(page)
            tables += _rule_tables(page, words, tables)
            flow = [w for w in words if not _word_in_table(w, tables)]
            pages.append(
                _Page(
                    page_number=page.page_number,
                    width=float(page.width),
                    height=float(page.height),
                    rows=_group_rows(flow, page.page_number),
                    tables=tables,
                )
            )

    chrome = _repeated_chrome(pages)
    for page_state in pages:
        page_state.rows = [
            row
            for row in page_state.rows
            if not (
                _in_band(row, page_state.height) is not None
                and _chrome_key(row.text) in chrome
            )
        ]

    if not any(p.rows or p.tables for p in pages):
        raise ExtractionEmpty(
            "PDF has no extractable text; OCR is out of scope",
            details={"url": source.url},
        )

    items: list[_Item] = []
    for page_state in pages:
        items.extend(_order_page(page_state))

    body_size, headings = _classify_headings(items, outline)
    builder = _Builder(items, headings)
    builder.build()

    removed = strip_invisible_blocks(builder.blocks)
    assign_block_ids(builder.blocks)
    warnings = _finalize_warnings(builder)
    if removed:
        warnings.append(invisible_text_warning(removed))
    warnings.extend(_limit_warnings(settings, truncated_pages, budget))
    if textless:
        warnings.append(_textless_warning(textless))

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
        title=_first_page_title(items, body_size) or info_title,
        lang=None,
        published_at=None,
        modified_at=None,
        jsonld=[],
    )
    return Document(
        document_id=make_document_id(source.url),
        version_id=make_version_id(make_document_id(source.url), source.content_hash),
        source=source,
        metadata=metadata,
        blocks=builder.blocks,
        warnings=warnings,
    )
