"""Tiny synthetic PDFs for the PDF heuristics tests (M10b, Phase P).

Built in memory with reportlab (BSD, already a dev dependency) in invariant
mode, so every build is byte-identical and nothing is committed. Words are
drawn ONE BY ONE at computed positions and no space character is ever
written: that is how pdfTeX sets text, and it is what glued the words in the
old extractor. The inter-word gap defaults to 0.26 font sizes (tight LaTeX
spacing, narrower than pdfplumber's 3 pt default tolerance).
"""

from __future__ import annotations

import io
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

PAGE_W, PAGE_H = A4
FONT = "Times-Roman"
BOLD = "Times-Bold"
MONO = "Courier"


@dataclass
class Pen:
    """Draws words without spaces; y is measured from the TOP of the page."""

    c: canvas.Canvas
    size: float = 10.0
    font: str = FONT
    gap_ratio: float = 0.26

    def width(self, text: str, font: str | None = None, size: float | None = None) -> float:
        return float(self.c.stringWidth(text, font or self.font, size or self.size))

    def words(
        self,
        words: Sequence[str],
        x: float,
        y_top: float,
        *,
        font: str | None = None,
        size: float | None = None,
        right: float | None = None,
    ) -> float:
        """Draw words from ``x``; justified to ``right`` when it is given."""
        font = font or self.font
        size = size or self.size
        widths = [self.width(w, font, size) for w in words]
        gap = self.gap_ratio * size
        if right is not None and len(words) > 1:
            gap = (right - x - sum(widths)) / (len(words) - 1)
        self.c.setFont(font, size)
        baseline = PAGE_H - y_top
        for word, w in zip(words, widths, strict=True):
            self.c.drawString(x, baseline, word)
            x += w + gap
        return x - gap

    def paragraph(
        self,
        lines: Sequence[str],
        x: float,
        right: float,
        y_top: float,
        *,
        indent: float = 0.0,
        leading: float = 12.0,
        font: str | None = None,
        size: float | None = None,
    ) -> float:
        """Justified lines (the last one ragged); returns the next y."""
        for i, line in enumerate(lines):
            last = i == len(lines) - 1
            self.words(
                line.split(),
                x + (indent if i == 0 else 0.0),
                y_top,
                font=font,
                size=size,
                right=None if last else right,
            )
            y_top += leading
        return y_top


@dataclass
class Doc:
    """A multi-page synthetic PDF: one callback per page."""

    pages: list[Callable[[Pen], None]] = field(default_factory=list)
    title: str | None = None
    outline: list[tuple[str, int, int]] = field(default_factory=list)  # (title, level, page)

    def build(self) -> bytes:
        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=A4, invariant=1)
        if self.title:
            c.setTitle(self.title)
        for number, draw in enumerate(self.pages, start=1):
            pen = Pen(c)
            draw(pen)
            for i, (title, level, page) in enumerate(self.outline):
                if page == number:
                    key = f"o{i}"
                    c.bookmarkPage(key)
                    c.addOutlineEntry(title, key, level=level - 1)
            c.showPage()
        c.save()
        return buf.getvalue()


LOREM = (
    "lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod "
    "tempor incididunt ut labore et dolore magna aliqua ut enim ad minim veniam "
    "quis nostrud exercitation ullamco laboris nisi ut aliquip ex ea commodo "
    "consequat duis aute irure dolor in reprehenderit in voluptate velit esse "
    "cillum dolore eu fugiat nulla pariatur excepteur sint occaecat cupidatat"
).split()


def lorem_lines(start: int, count: int, per_line: int = 14) -> list[str]:
    """Deterministic filler lines cycling through ``LOREM``."""
    out: list[str] = []
    pos = start
    for _ in range(count):
        out.append(" ".join(LOREM[(pos + k) % len(LOREM)] for k in range(per_line)))
        pos += per_line
    return out
