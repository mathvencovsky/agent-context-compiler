"""Pydantic v2 models of the intermediate representation (IR) — SPEC §4.

The IR is the single structured representation every output format is derived
from: Markdown is generated from the IR, never from HTML (SPEC §4), and chunks
and evidence quotes read from it too. The models here mirror SPEC §4 field for
field, so that downstream features (HTML walker, PDF walker, to_markdown,
chunker) share one contract.

Identifier and normalization helpers also live here because they define the
stable-id contract the whole pipeline depends on:

- ``make_document_id`` / ``make_version_id`` build the ``doc_`` / ``ver_``
  prefixed ids (SPEC §4). A version is one (document, raw bytes, extractor
  version) triple: :data:`EXTRACTOR_VERSION` is part of its identity, so an
  extractor upgrade yields a new version of unchanged bytes instead of serving
  the old extraction forever (DECISIONS.md 2026-10-29).
- ``normalize_text`` produces the markup-free, whitespace-collapsed text that
  every block carries EXCEPT ``code`` (whose bytes are preserved verbatim per
  SPEC §4).
- ``base_block_id`` implements the SPEC §4 ``block_id`` hash.
- ``assign_block_ids`` applies the stable disambiguation suffix scheme recorded
  in DECISIONS.md (2026-10-11, "M3 — block_id estável sob edição"): the suffix
  tracks the occurrence index among blocks sharing the same
  ``heading_path | type | text`` key, counted in document order. Because the
  counter is keyed by content, editing a block (giving it a new key) changes
  only that block's id and leaves every distinct block around it untouched —
  the M3 acceptance criterion.

No LLM and no network here: everything is deterministic and pure (CLAUDE.md).
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

BlockType = Literal[
    "heading",
    "paragraph",
    "list",
    "code",
    "table",
    "blockquote",
    "admonition",
    "definition_list",
]
"""The closed set of block kinds the IR can represent (SPEC §4)."""

RenderMode = Literal["http", "browser", "pdf"]
"""How the source bytes were obtained (SPEC §4)."""


class Link(BaseModel):
    """A hyperlink extracted from a block (SPEC §4)."""

    text: str
    href: str  # absolute URL


class SourceInfo(BaseModel):
    """Provenance of the fetched bytes a document was built from (SPEC §4)."""

    url: str  # requested URL, normalized
    final_url: str  # after redirects
    content_type: str
    fetched_at: datetime
    etag: str | None
    last_modified: str | None
    content_hash: str  # sha256 of the raw body
    render_mode: RenderMode
    # Set only when the blocks were extracted from the page's same-origin
    # Markdown alternate (SPEC §3.5): its final URL and the sha256 of its body.
    # Omitted from every serialization when unset, so documents that do not use
    # an alternate serialize exactly as before (M10b).
    alternate_url: str | None = Field(default=None, exclude_if=lambda v: v is None)
    alternate_content_hash: str | None = Field(
        default=None, exclude_if=lambda v: v is None
    )


class Metadata(BaseModel):
    """Document-level metadata and raw JSON-LD blocks (SPEC §4)."""

    title: str | None
    lang: str | None
    published_at: str | None
    modified_at: str | None
    jsonld: list[dict[str, object]]  # JSON-LD blocks found, unchanged


class Block(BaseModel):
    """A single structural unit of the document (SPEC §4).

    ``data`` carries the type-specific payload described in SPEC §4 (and, for
    ``blockquote`` and ``definition_list``, in DECISIONS.md 2026-10-11). ``text``
    is normalized and markup-free for every type except ``code``, whose source
    bytes are preserved verbatim in ``data["code"]``.
    """

    block_id: str  # stable, see base_block_id / assign_block_ids
    type: BlockType
    order: int  # position within the document
    level: int | None  # heading only (1..6)
    heading_path: list[str]  # ancestor titles, highest to nearest
    text: str  # normalized text, no markup
    data: dict[str, object]  # type-specific payload
    links: list[Link]
    page: int | None  # PDF only, starting at 1


class Warning(BaseModel):
    """A non-fatal issue surfaced during extraction (SPEC §4)."""

    code: str  # e.g. "table_unparsed", "low_text_content"
    message: str
    block_id: str | None


class Document(BaseModel):
    """The complete IR for one version of one source (SPEC §4)."""

    document_id: str  # "doc_" + sha1(normalized url)[:16]
    version_id: str  # make_version_id(document_id, content_hash, EXTRACTOR_VERSION)
    source: SourceInfo
    metadata: Metadata
    blocks: list[Block]
    warnings: list[Warning]


def make_document_id(normalized_url: str) -> str:
    """Return the stable document id for a normalized URL (SPEC §4).

    ``"doc_" + sha1(normalized_url)[:16]``.
    """
    digest = hashlib.sha1(normalized_url.encode()).hexdigest()
    return "doc_" + digest[:16]


#: Version of the deterministic extraction pipeline (HTML, PDF, Markdown,
#: plain text, and the chunks derived from their IR). Bump it in the same commit
#: as ANY change that alters extraction output for unchanged bytes: the stored
#: versions built by an older extractor are then re-extracted from their stored
#: raw bytes on the next resolve, under a new ``version_id`` (SPEC §4, §5;
#: DECISIONS.md 2026-10-29). History:
#:
#: * (none) — rows written before migration 0006; treated as outdated.
#: * ``"2"`` — M10b Phase P: PDF extraction rewritten, HTML/code cleanup,
#:   Markdown and plain-text sources.
EXTRACTOR_VERSION = "2"


def current_extractor_version() -> str:
    """Return :data:`EXTRACTOR_VERSION`, read at call time (tests patch it)."""
    return EXTRACTOR_VERSION


def make_version_id(
    document_id: str, content_hash: str, extractor_version: str | None = None
) -> str:
    """Return the version id of one extraction of one snapshot (SPEC §4).

    ``"ver_" + sha256(document_id + "\n" + content_hash + "\n" +
    extractor_version)[:16]`` where ``content_hash`` is the sha256 hex digest of
    the raw body and ``extractor_version`` defaults to
    :data:`EXTRACTOR_VERSION`. Deterministic: the same page bytes extracted by
    the same extractor always get the same id. The ``document_id`` keeps two
    URLs that serve identical bytes from sharing (and overwriting) one version
    row and its blobs; the extractor version gives an upgraded extraction of
    unchanged bytes its own id, so every chunk and fragment belongs to exactly
    one (snapshot, extractor version).
    """
    version = (
        extractor_version
        if extractor_version is not None
        else current_extractor_version()
    )
    key = f"{document_id}\n{content_hash}\n{version}"
    return "ver_" + hashlib.sha256(key.encode()).hexdigest()[:16]


_WHITESPACE_RE = re.compile(r"\s+")


def normalize_text(s: str) -> str:
    """Collapse whitespace and strip surrounding blanks (SPEC §4).

    The input is assumed to already be markup-free text (the HTML/PDF walkers
    extract the text content before calling this). All runs of whitespace —
    including newlines and tabs — collapse to a single space, and the result is
    stripped. Code blocks are NEVER passed through here: their bytes are
    preserved verbatim (SPEC §4).
    """
    return _WHITESPACE_RE.sub(" ", s).strip()


def base_block_id(
    heading_path: list[str],
    block_type: BlockType,
    normalized_text: str,
) -> str:
    """Return the un-disambiguated block id (SPEC §4).

    ``"b_" + sha1(" > ".join(heading_path) + "|" + type + "|" + text)[:12]``.
    The ``normalized_text`` is the block's ``text`` (already normalized, or the
    verbatim code for ``code`` blocks). Disambiguation of repeats within a
    document is applied separately by ``assign_block_ids``.
    """
    key = " > ".join(heading_path) + "|" + block_type + "|" + normalized_text
    digest = hashlib.sha1(key.encode()).hexdigest()
    return "b_" + digest[:12]


def assign_block_ids(blocks: list[Block]) -> None:
    """Assign stable ``block_id`` values in place, disambiguating repeats.

    For each block the base id comes from ``base_block_id(heading_path, type,
    text)``. When several blocks in the same document share the same base id
    (identical ``heading_path | type | text``), the first keeps the base id and
    later ones get ``_2``, ``_3`` ... in document order.

    The suffix is anchored to the occurrence index among blocks sharing the SAME
    key, counted in document order (DECISIONS.md 2026-10-11, "M3 — block_id
    estável sob edição"). The counter is keyed by CONTENT, not by a global
    running position, so inserting, removing or editing a block with a DIFFERENT
    key anywhere in the document does not shift the suffix of any identical
    block. In particular, editing one paragraph (which gives it a new key)
    changes only that block's id and preserves the ids of all the distinct
    blocks around it — the SPEC §4 / ROADMAP M3 acceptance criterion. Stability
    among IDENTICAL copies is local to earlier occurrences: editing or removing
    one of N identical copies preserves the id of every copy that precedes it in
    document order (later copies shift down by one, since the occurrence index is
    by definition relative to the surviving identical copies). A scheme that also
    pins the later identical copies would require an external per-version anchor,
    which is out of scope for the stateless, deterministic extract path.
    """
    seen: dict[str, int] = {}
    for block in blocks:
        base = base_block_id(block.heading_path, block.type, block.text)
        count = seen.get(base, 0) + 1
        seen[base] = count
        block.block_id = base if count == 1 else f"{base}_{count}"
