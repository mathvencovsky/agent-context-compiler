"""block_id stability: idempotent ids + single-paragraph edit isolation (FEAT-004).

Two SPEC §4 / ROADMAP-M3 acceptance properties, exercised on the real fixtures:

1. Extracting the SAME page twice yields identical ``block_id`` lists (the whole
   extract path is deterministic — no timestamps, no unordered iteration).
2. Editing exactly ONE paragraph's text (giving that block a new content key)
   changes only THAT block's id; every other block's id is unchanged. This is the
   content-keyed disambiguation recorded in DECISIONS 2026-10-11 ("M3 — block_id
   estável sob edição").

The edit is done on an in-memory copy of the fixture HTML: we parse it, pick one
paragraph that has distinctive, unique text, change its text, and re-serialize —
so only one block's content key moves.
"""

from __future__ import annotations

import lxml.html

from app.extract.html import extract_html
from app.extract.ir import Document
from tests.unit.extract._fixtures import read_fixture, source_for

# A representative long guide with plenty of distinct paragraphs.
_EDIT_FIXTURE = "python_tutorial_classes"


def _block_ids(doc: Document) -> list[str]:
    return [b.block_id for b in doc.blocks]


def test_same_page_twice_is_stable() -> None:
    body = read_fixture(_EDIT_FIXTURE)
    source = source_for(_EDIT_FIXTURE, body)
    first = extract_html(body, source)
    second = extract_html(body, source)
    assert _block_ids(first) == _block_ids(second)


def _edit_one_paragraph(body: bytes) -> tuple[bytes, str]:
    """Return (edited_html, new_text) with exactly one paragraph's text changed.

    Picks the first paragraph in the main content whose normalized text is unique
    among all paragraphs (so the edit cannot accidentally collide with another
    block's content key), and replaces its text with a sentinel string.
    """
    text = body.decode("utf-8", errors="replace")
    tree = lxml.html.fromstring(text)

    # Count paragraph texts so we only edit a UNIQUE one.
    seen: dict[str, int] = {}
    paragraphs = list(tree.iter("p"))
    for p in paragraphs:
        key = (p.text_content() or "").strip()
        seen[key] = seen.get(key, 0) + 1

    sentinel = "NINA sentinel edited paragraph marker for block_id stability test."
    for p in paragraphs:
        key = (p.text_content() or "").strip()
        if key and seen[key] == 1:
            # Replace the paragraph's content with the sentinel text.
            for child in list(p):
                p.remove(child)
            p.text = sentinel
            break
    else:  # pragma: no cover - fixtures always have a unique paragraph
        raise AssertionError("no unique paragraph found to edit")

    edited = lxml.html.tostring(tree, encoding="unicode")
    return edited.encode("utf-8"), sentinel


def test_editing_one_paragraph_changes_only_that_block_id() -> None:
    body = read_fixture(_EDIT_FIXTURE)
    source = source_for(_EDIT_FIXTURE, body)

    original = extract_html(body, source)

    edited_body, sentinel = _edit_one_paragraph(body)
    edited = extract_html(edited_body, source)

    # The two documents must have the same block structure (same count/types),
    # since only one paragraph's text changed.
    assert len(original.blocks) == len(edited.blocks)
    assert [b.type for b in original.blocks] == [b.type for b in edited.blocks]

    differing = [
        (o.block_id, e.block_id)
        for o, e in zip(original.blocks, edited.blocks, strict=True)
        if o.block_id != e.block_id
    ]

    # Exactly one block changed its id, and it is the edited paragraph.
    assert len(differing) == 1, f"expected 1 changed id, got {differing}"
    edited_block = next(
        b
        for o, b in zip(original.blocks, edited.blocks, strict=True)
        if sentinel in b.text
    )
    assert edited_block.type == "paragraph"
    assert sentinel in edited_block.text
