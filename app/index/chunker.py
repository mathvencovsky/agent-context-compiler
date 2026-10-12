"""IR -> chunks (SPEC §5). Deterministic, no LLM, no network (CLAUDE.md).

``chunk_document`` turns a :class:`~app.extract.ir.Document` into a list of
:class:`Chunk` ready for indexing (FEAT-002). The algorithm (SPEC §5):

1. Group CONSECUTIVE blocks sharing the same ``heading_path`` into sections.
2. Within a section, accumulate blocks toward a 200-500 token target, with a
   hard maximum of ``_MAX_TOKENS`` (800) measured over the FINAL ``text`` (i.e.
   including the section-path prefix). A block that would push the running chunk
   over the max starts a new chunk.
3. ``table`` and ``code`` blocks are never split, UNLESS a single such block
   alone exceeds the max — then it is split: a table by ROWS (repeating
   ``data["headers"]`` in every part) and code by BLANK LINES (grouping runs of
   lines up to the max). Each part becomes its own chunk.

Ambiguities resolved (recorded in DECISIONS.md): the section-path prefix uses
``" > "`` as separator and ``"\n\n"`` between prefix and content; the 800-token
limit is measured over the prefixed ``text``; a non-table/non-code block that
alone exceeds the max is emitted as a single oversized chunk (the SPEC defines
no prose-splitting rule).

Every chunk carries non-empty ``block_ids`` and a ``heading_path`` coherent with
its source blocks (provenance, CLAUDE.md). For parts of a split oversized block
the source ``block_id`` is repeated so provenance stays non-empty.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import cast

from app.extract.ir import Block, Document, Metadata, SourceInfo
from app.extract.to_markdown import to_markdown
from app.index.tokens import count_tokens

#: Target chunk size window and hard maximum, in cl100k_base tokens (SPEC §5).
_TARGET_MIN_TOKENS = 200
_TARGET_MAX_TOKENS = 500
_MAX_TOKENS = 800


@dataclass(frozen=True)
class Chunk:
    """One indexed chunk derived from a run of IR blocks (SPEC §5)."""

    version_id: str
    block_ids: list[str]
    heading_path: list[str]
    text: str  # section-path prefix + rendered content (what gets indexed)
    md: str  # Markdown rendering of the chunk's blocks (to_markdown)
    tokens: int  # count_tokens(text)
    ord: int  # 0-based position of the chunk in the document


def _prefix(heading_path: list[str]) -> str:
    """Return the ``"A > B > C\\n\\n"`` section prefix, or ``""`` if empty."""
    if not heading_path:
        return ""
    return " > ".join(heading_path) + "\n\n"


# Minimal, valid IR scaffolding for the synthetic Documents handed to
# ``to_markdown``: the serializer reads only ``doc.blocks``, but Pydantic still
# validates the model, so ``source``/``metadata`` must be well-formed.
_SYNTHETIC_SOURCE = SourceInfo(
    url="",
    final_url="",
    content_type="",
    fetched_at=datetime(1970, 1, 1),  # noqa: DTZ001 — placeholder, never surfaced
    etag=None,
    last_modified=None,
    content_hash="",
    render_mode="http",
)
_SYNTHETIC_METADATA = Metadata(
    title=None, lang=None, published_at=None, modified_at=None, jsonld=[]
)


def _render(blocks: list[Block], version_id: str) -> str:
    """Render ``blocks`` to Markdown via the shared IR serializer."""
    doc = Document(
        document_id="",
        version_id=version_id,
        source=_SYNTHETIC_SOURCE,
        metadata=_SYNTHETIC_METADATA,
        blocks=blocks,
        warnings=[],
    )
    return to_markdown(doc)


def _make_chunk(
    blocks: list[Block],
    *,
    version_id: str,
    heading_path: list[str],
    ord: int,
    block_ids: list[str] | None = None,
) -> Chunk:
    """Build a :class:`Chunk` from ``blocks`` (prefix applied to ``text``)."""
    md = _render(blocks, version_id)
    text = _prefix(heading_path) + md
    ids = block_ids if block_ids is not None else [b.block_id for b in blocks]
    return Chunk(
        version_id=version_id,
        block_ids=ids,
        heading_path=heading_path,
        text=text,
        md=md,
        tokens=count_tokens(text),
        ord=ord,
    )


def _chunk_tokens(blocks: list[Block], heading_path: list[str], version_id: str) -> int:
    """Token count of the final (prefixed) text for ``blocks``."""
    return count_tokens(_prefix(heading_path) + _render(blocks, version_id))


def _split_oversized_block(block: Block, version_id: str) -> list[list[Block]]:
    """Split a single ``table``/``code`` block that alone exceeds the max.

    Returns a list of block-groups (each a single synthetic block) each fitting
    under ``_MAX_TOKENS`` where possible. Non-table/non-code blocks are returned
    whole (the SPEC defines no prose split).
    """
    if block.type == "table":
        return [[part] for part in _split_table(block)]
    if block.type == "code":
        return [[part] for part in _split_code(block)]
    return [[block]]


def _synthetic_block(block: Block, data: dict[str, object]) -> Block:
    """Clone ``block`` with new ``data`` (keeps block_id for provenance)."""
    return block.model_copy(update={"data": data})


def _split_table(block: Block) -> list[Block]:
    """Split a table block by rows, repeating ``headers`` in every part."""
    headers = cast("list[str]", block.data.get("headers", []))
    rows = cast("list[list[str]]", block.data.get("rows", []))
    base_data = {k: v for k, v in block.data.items() if k != "rows"}
    base_data.setdefault("caption", None)

    if not rows:
        return [block]

    parts: list[Block] = []
    current: list[list[str]] = []
    for row in rows:
        candidate = [*current, row]
        trial = _synthetic_block(
            block, {**base_data, "headers": headers, "rows": candidate}
        )
        if current and count_tokens(_render([trial], block.block_id)) > _MAX_TOKENS:
            parts.append(
                _synthetic_block(
                    block, {**base_data, "headers": headers, "rows": current}
                )
            )
            current = [row]
        else:
            current = candidate
    if current:
        parts.append(
            _synthetic_block(block, {**base_data, "headers": headers, "rows": current})
        )
    return parts


def _split_code(block: Block) -> list[Block]:
    """Split a code block by blank lines, grouping runs up to the max."""
    code = cast("str", block.data.get("code", ""))
    language = block.data.get("language")
    base_data = {k: v for k, v in block.data.items() if k != "code"}

    # Segments are runs of lines delimited by blank lines; blank lines are kept
    # as separators so rejoining reproduces the original structure.
    lines = code.split("\n")
    segments: list[str] = []
    buf: list[str] = []
    for line in lines:
        buf.append(line)
        if line.strip() == "":
            segments.append("\n".join(buf))
            buf = []
    if buf:
        segments.append("\n".join(buf))

    parts: list[Block] = []
    current = ""
    for seg in segments:
        candidate = seg if not current else current + "\n" + seg
        trial = _synthetic_block(
            block, {**base_data, "language": language, "code": candidate}
        )
        if current and count_tokens(_render([trial], block.block_id)) > _MAX_TOKENS:
            parts.append(
                _synthetic_block(
                    block, {**base_data, "language": language, "code": current}
                )
            )
            current = seg
        else:
            current = candidate
    if current:
        parts.append(
            _synthetic_block(
                block, {**base_data, "language": language, "code": current}
            )
        )
    return parts or [block]


def _sections(blocks: list[Block]) -> list[tuple[list[str], list[Block]]]:
    """Group consecutive blocks sharing the same ``heading_path``."""
    sections: list[tuple[list[str], list[Block]]] = []
    for block in blocks:
        if sections and sections[-1][0] == block.heading_path:
            sections[-1][1].append(block)
        else:
            sections.append((block.heading_path, [block]))
    return sections


def _plan_section(
    heading_path: list[str],
    section_blocks: list[Block],
    version_id: str,
) -> list[tuple[list[Block], list[str]]]:
    """Return ordered (blocks, block_ids) groups for one section.

    Each group becomes a chunk. Atomic blocks (table/code) that alone exceed the
    max are split into their own groups; otherwise blocks accumulate toward the
    target window up to the hard max.
    """
    groups: list[tuple[list[Block], list[str]]] = []
    current: list[Block] = []

    def flush() -> None:
        if current:
            groups.append((list(current), [b.block_id for b in current]))
            current.clear()

    for block in section_blocks:
        # An atomic block (table/code) that alone exceeds the max is split into
        # its own groups; whatever is buffered so far is flushed first.
        if (
            block.type in ("table", "code")
            and _chunk_tokens([block], heading_path, version_id) > _MAX_TOKENS
        ):
            flush()
            for part_blocks in _split_oversized_block(block, version_id):
                groups.append((part_blocks, [block.block_id]))
            continue

        # Would adding this block push the running chunk over the hard max?
        trial = [*current, block]
        if current and _chunk_tokens(trial, heading_path, version_id) > _MAX_TOKENS:
            flush()
        current.append(block)
        # Reached the target window: close the chunk at a natural point.
        if _chunk_tokens(current, heading_path, version_id) >= _TARGET_MIN_TOKENS:
            flush()

    flush()
    return groups


def chunk_document(doc: Document) -> list[Chunk]:
    """Chunk ``doc`` into indexable :class:`Chunk` list (SPEC §5)."""
    version_id = doc.version_id
    chunks: list[Chunk] = []
    for heading_path, section_blocks in _sections(doc.blocks):
        for blocks, block_ids in _plan_section(
            heading_path, section_blocks, version_id
        ):
            chunks.append(
                _make_chunk(
                    blocks,
                    version_id=version_id,
                    heading_path=heading_path,
                    ord=len(chunks),
                    block_ids=block_ids,
                )
            )
    return chunks
