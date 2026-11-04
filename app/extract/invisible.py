"""Strip invisible Unicode and control characters from extracted text.

Indirect prompt injection hides instructions in characters a human reader
never sees but a model reads: Unicode tag characters (U+E0000-U+E007F, "ASCII
smuggling"), bidirectional controls (Trojan Source), zero-width characters,
ANSI escape sequences and other C0/C1 controls. They carry no documentation
content, so extraction removes them from every text field of every block and
says so with ONE ``invisible_text_removed`` warning carrying the count.

Kept on purpose: the single-character direction MARKS U+200E (LRM), U+200F
(RLM) and U+061C (ALM): real documentation uses them (the FastAPI release
notes fixture has three LRMs) and, unlike the embeddings/overrides/isolates
(U+202A-U+202E, U+2066-U+2069) that are removed, they cannot reorder text.
Also kept: ``\\t``, ``\\n``, ``\\r``, ``\\v`` and ``\\f`` (whitespace that
normalization already handles), and U+200C/U+200D (ZWNJ/ZWJ), which some
scripts and emoji sequences need.

A document with nothing to strip is returned untouched (same objects, same
text), so normal pages extract exactly as before.
"""

from __future__ import annotations

import re

from app.extract.ir import Block, Warning, normalize_text

INVISIBLE_TEXT_REMOVED_WARNING_CODE = "invisible_text_removed"

_ANSI = (
    r"\x1b\[[0-?]*[ -/]*[@-~]"  # ANSI CSI sequence (colors, cursor moves)
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?"  # ANSI OSC sequence
    r"|\x1b[@-Z\\-_]?"  # other ESC sequences, or a lone ESC
)
_ANSI_RE = re.compile(_ANSI)
_INVISIBLE = re.compile(
    _ANSI + r"|[\U000E0000-\U000E007F]"  # Unicode tag characters
    r"|[\u202A-\u202E\u2066-\u2069]"  # bidi embeddings, overrides, isolates
    r"|[\u200B\u2060\uFEFF]"  # zero-width space, word joiner, BOM
    r"|[\x00-\x08\x0e-\x1a\x1c-\x1f\x7f-\x9f]"  # other C0 (but ESC) and C1
)


def strip_ansi(text: str) -> tuple[str, int]:
    """``text`` without ANSI escapes, and the removed length.

    Run on raw HTML before parsing: lxml drops a bare ESC while parsing, which
    would leave the rest of the sequence (``[2J``) behind as visible text.
    """
    clean = _ANSI_RE.sub("", text)
    return clean, len(text) - len(clean)


def strip_invisible(text: str) -> tuple[str, int]:
    """``text`` without invisible/control characters, and how many were removed.

    ``count`` is the number of removed characters (an ANSI sequence counts its
    full length).
    """
    removed = 0

    def _drop(match: re.Match[str]) -> str:
        nonlocal removed
        removed += len(match.group(0))
        return ""

    if not _INVISIBLE.search(text):
        return text, 0
    return _INVISIBLE.sub(_drop, text), removed


def _clean_normalized(text: str) -> tuple[str, int]:
    clean, n = strip_invisible(text)
    return (normalize_text(clean), n) if n else (text, 0)


def _clean_value(value: object) -> tuple[object, int]:
    """Strip every string inside a block's ``data`` (kept verbatim otherwise:
    list indentation and code whitespace are structure)."""
    if isinstance(value, str):
        return strip_invisible(value)
    if isinstance(value, list):
        total = 0
        out: list[object] = []
        for item in value:
            cleaned, n = _clean_value(item)
            total += n
            out.append(cleaned)
        return (out, total) if total else (value, 0)
    if isinstance(value, dict):
        total = 0
        new: dict[str, object] = {}
        for key, item in value.items():
            cleaned, n = _clean_value(item)
            total += n
            new[key] = cleaned
        return (new, total) if total else (value, 0)
    return value, 0


def strip_invisible_blocks(blocks: list[Block]) -> int:
    """Strip invisible characters from every block in place; return the count.

    Run BEFORE ``assign_block_ids`` so ids hash the cleaned text. Code keeps its
    whitespace verbatim (only the invisible characters go); ``text``, heading
    titles and link texts are re-normalized after stripping; ``data`` strings
    are stripped verbatim.
    """
    total = 0
    for block in blocks:
        verbatim = block.type == "code"
        text, n = (
            strip_invisible(block.text) if verbatim else _clean_normalized(block.text)
        )
        if n:
            block.text = text
            total += n
        path: list[str] = []
        path_removed = 0
        for title in block.heading_path:
            clean, n = _clean_normalized(title)
            path.append(clean)
            path_removed += n
        if path_removed:
            block.heading_path = path
            total += path_removed
        data, n = _clean_value(block.data)
        if n:
            block.data = data  # type: ignore[assignment]
            total += n
        for link in block.links:
            clean, n = _clean_normalized(link.text)
            if n:
                link.text = clean
                total += n
    return total


def invisible_text_warning(count: int) -> Warning:
    """The single warning for a document that had invisible text removed."""
    return Warning(
        code=INVISIBLE_TEXT_REMOVED_WARNING_CODE,
        message=(
            f"removed {count} invisible or control character(s) from the "
            "extracted text (Unicode tags, bidi controls, zero-width characters, "
            "ANSI escapes or other control characters)"
        ),
        block_id=None,
    )
