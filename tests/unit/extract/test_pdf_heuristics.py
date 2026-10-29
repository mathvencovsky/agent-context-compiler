"""Unit tests for the PDF extraction heuristics (M10b, Phase P step 1).

Each test builds a tiny synthetic PDF in memory (``_pdf_synth``: reportlab,
words drawn one by one with no space characters, as pdfTeX does) and checks
one heuristic of ``app/extract/pdf.py``: word spacing, paragraph assembly,
line-end hyphenation, two-column reading order, table filtering, rule-bounded
tables, the outline, the title, page chrome, code blocks and the
``pages_without_text`` warning. No network, no fixtures on disk.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from app.extract.ir import Document, SourceInfo
from app.extract.pdf import PAGES_WITHOUT_TEXT_WARNING_CODE, extract_pdf
from tests.unit.extract._pdf_synth import (
    BOLD,
    MONO,
    PAGE_W,
    Doc,
    Pen,
    lorem_lines,
)

_LEFT, _RIGHT = 72.0, PAGE_W - 72.0


def _extract(raw: bytes) -> Document:
    source = SourceInfo(
        url="https://fixtures.nina.test/synthetic.pdf",
        final_url="https://fixtures.nina.test/synthetic.pdf",
        content_type="application/pdf",
        fetched_at=datetime(2026, 10, 29, tzinfo=UTC),
        etag=None,
        last_modified=None,
        content_hash=hashlib.sha256(raw).hexdigest(),
        render_mode="pdf",
    )
    return extract_pdf(raw, source)


def _text_blocks(doc: Document) -> list[str]:
    return [b.text for b in doc.blocks if b.type == "paragraph"]


def test_words_drawn_without_spaces_are_not_glued() -> None:
    def page(pen: Pen) -> None:
        pen.words(
            "Register x0 is hardwired with all bits equal to zero.".split(), _LEFT, 100
        )

    doc = _extract(Doc([page]).build())
    assert _text_blocks(doc) == [
        "Register x0 is hardwired with all bits equal to zero."
    ]


def test_overprinted_fake_bold_glyphs_are_dropped() -> None:
    """A word drawn twice 0.3 pt apart (fake bold) reads once, not doubled."""
    text = "Overprinted words read once."

    def page(pen: Pen) -> None:
        pen.words(text.split(), _LEFT, 100)
        pen.words(text.split(), _LEFT + 0.3, 100)

    doc = _extract(Doc([page]).build())
    assert _text_blocks(doc) == [text]


def test_lines_join_into_paragraphs_split_by_first_line_indent() -> None:
    first = lorem_lines(0, 4)
    second = lorem_lines(40, 3)

    def page(pen: Pen) -> None:
        y = pen.paragraph(first, _LEFT, _RIGHT, 100, indent=15)
        pen.paragraph(second, _LEFT, _RIGHT, y, indent=15)  # no extra space

    blocks = _text_blocks(_extract(Doc([page]).build()))
    assert blocks == [" ".join(first), " ".join(second)]


def test_vertical_gap_and_short_last_line_end_a_paragraph() -> None:
    first = lorem_lines(0, 3)
    second = lorem_lines(30, 2)

    def page(pen: Pen) -> None:
        y = pen.paragraph(first, _LEFT, _RIGHT, 100)
        pen.paragraph(second, _LEFT, _RIGHT, y + 10)  # no indent, blank space

    assert len(_text_blocks(_extract(Doc([page]).build()))) == 2


def test_line_end_hyphen_is_removed_inside_a_word() -> None:
    def page(pen: Pen) -> None:
        pen.paragraph(
            [
                "the decoder reads every instruction and checks the exam-",
                "ple given in the out-of-",
                "order section of this manual",
            ],
            _LEFT,
            _RIGHT,
            100,
        )

    (text,) = _text_blocks(_extract(Doc([page]).build()))
    assert "the example given" in text
    assert "out-of-order section" in text


def test_two_columns_are_read_left_then_right() -> None:
    mid = PAGE_W / 2
    left_lines = lorem_lines(0, 12, per_line=6)
    right_lines = lorem_lines(60, 12, per_line=6)
    left_lines[0] = "leftstart " + left_lines[0].split(" ", 1)[1]
    right_lines[0] = "rightstart " + right_lines[0].split(" ", 1)[1]
    left_lines[-1] = "the left column ends here."
    right_lines[-1] = "the right column ends here."

    def page(pen: Pen) -> None:
        pen.words("A Two Column Title Spanning The Page".split(), 180, 80, size=16)
        pen.paragraph(left_lines, _LEFT, mid - 10, 120)
        pen.paragraph(right_lines, mid + 10, _RIGHT, 120)

    doc = _extract(Doc([page]).build())
    text = "\n".join(b.text for b in doc.blocks)
    assert text.index("Spanning") < text.index("leftstart") < text.index("rightstart")
    # Each column is one paragraph, not one block per visual line.
    assert len(_text_blocks(doc)) == 2


def test_frame_around_text_and_empty_grid_are_not_tables() -> None:
    def page(pen: Pen) -> None:
        pen.paragraph(lorem_lines(0, 3), _LEFT, _RIGHT, 100)
        pen.c.rect(_LEFT - 4, 841.89 - 140, _RIGHT - _LEFT + 8, 52)  # frame
        for row in range(3):  # an empty 3x3 ruled grid
            for col in range(3):
                pen.c.rect(100 + col * 60, 400 - row * 20, 60, 20)

    doc = _extract(Doc([page]).build())
    assert not [b for b in doc.blocks if b.type == "table"]
    assert _text_blocks(doc) == [" ".join(lorem_lines(0, 3))]


def test_table_bounded_by_horizontal_rules_is_found() -> None:
    rows = [
        ["Country", "Capital", "Language"],
        ["Austria", "Vienna", "German"],
        ["Finland", "Helsinki", "Finnish, Swedish"],
        ["Belgium", "Brussels", "Dutch, French"],
    ]
    xs = [100.0, 220.0, 340.0]

    def page(pen: Pen) -> None:
        top = 841.89
        pen.c.line(90, top - 190, 480, top - 190)
        y = 205.0
        for i, row in enumerate(rows):
            for x, cell in zip(xs, row, strict=True):
                pen.words(cell.split(), x, y)
            if i == 0:
                pen.c.line(90, top - (y + 5), 480, top - (y + 5))
            y += 14
        pen.c.line(90, top - (y - 6), 480, top - (y - 6))

    tables = [b for b in _extract(Doc([page]).build()).blocks if b.type == "table"]
    assert len(tables) == 1
    assert tables[0].data["headers"] == rows[0]
    assert ["Finland", "Helsinki", "Finnish, Swedish"] in tables[0].data["rows"]


def test_outline_sets_heading_levels_and_metadata_title() -> None:
    def page(pen: Pen) -> None:
        pen.words("The Synthetic Manual".split(), _LEFT, 80, size=20)
        pen.words(["1", "Getting", "Started"], _LEFT, 120, font=BOLD, size=12)
        pen.paragraph(lorem_lines(0, 2), _LEFT, _RIGHT, 140)
        pen.words(["1.1", "Installing"], _LEFT, 180, font=BOLD, size=11)
        pen.paragraph(lorem_lines(20, 2), _LEFT, _RIGHT, 200)

    doc = _extract(
        Doc(
            [page],
            title="synthetic.dvi",  # a junk /Title: the page-1 title wins
            outline=[("Getting Started", 1, 1), ("Installing", 2, 1)],
        ).build()
    )
    assert doc.metadata.title == "The Synthetic Manual"
    headings = {b.text: b.level for b in doc.blocks if b.type == "heading"}
    assert headings["1 Getting Started"] == 1
    assert headings["1.1 Installing"] == 2
    install = next(
        b for b in doc.blocks if b.text.startswith(lorem_lines(20, 1)[0][:20])
    )
    assert install.heading_path[-1] == "1.1 Installing"


def test_info_title_is_used_when_page_one_has_no_title() -> None:
    def page(pen: Pen) -> None:
        pen.paragraph(lorem_lines(0, 3), _LEFT, _RIGHT, 100)

    doc = _extract(Doc([page], title="Configuration Reference").build())
    assert doc.metadata.title == "Configuration Reference"


def test_bare_page_numbers_are_dropped_as_chrome() -> None:
    def make(n: int):  # type: ignore[no-untyped-def]
        def page(pen: Pen) -> None:
            pen.paragraph(lorem_lines(n * 10, 3), _LEFT, _RIGHT, 150)
            pen.words([str(n)], PAGE_W / 2, 800)

        return page

    doc = _extract(Doc([make(1), make(2), make(3)]).build())
    assert all(b.text not in {"1", "2", "3"} for b in doc.blocks)


def test_monospace_lines_become_one_code_block_with_spacing() -> None:
    code = ["def add(a, b):", "    return a + b"]

    def page(pen: Pen) -> None:
        pen.paragraph(lorem_lines(0, 2), _LEFT, _RIGHT, 100)
        char_w = pen.width("x", MONO, 10)
        for i, line in enumerate(code):
            indent = (len(line) - len(line.lstrip())) * char_w
            x = _LEFT + indent
            for word in line.split():
                pen.c.setFont(MONO, 10)
                pen.c.drawString(x, 841.89 - (140 + 12 * i), word)
                x += pen.width(word + " ", MONO, 10)

    blocks = [b for b in _extract(Doc([page]).build()).blocks if b.type == "code"]
    assert len(blocks) == 1
    assert blocks[0].data["code"] == "\n".join(code)


def test_page_with_drawing_but_no_text_layer_warns() -> None:
    def text_page(pen: Pen) -> None:
        pen.paragraph(lorem_lines(0, 3), _LEFT, _RIGHT, 100)

    def scanned_page(pen: Pen) -> None:
        pen.c.rect(100, 100, 300, 400, fill=1)  # stands in for a scanned image

    def blank_page(pen: Pen) -> None:
        return None

    doc = _extract(Doc([text_page, scanned_page, blank_page]).build())
    warnings = [w for w in doc.warnings if w.code == PAGES_WITHOUT_TEXT_WARNING_CODE]
    assert len(warnings) == 1
    assert "pages 2)" in warnings[0].message  # the blank page 3 is not listed
    assert all(b.page == 1 for b in doc.blocks)


def test_extraction_is_deterministic() -> None:
    def page(pen: Pen) -> None:
        pen.words("Deterministic Title".split(), _LEFT, 80, size=18)
        pen.paragraph(lorem_lines(0, 5), _LEFT, _RIGHT, 120, indent=15)

    raw = Doc([page]).build()
    assert raw == Doc([page]).build()
    assert _extract(raw).model_dump() == _extract(raw).model_dump()
