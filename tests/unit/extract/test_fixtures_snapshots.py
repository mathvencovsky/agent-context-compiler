"""Syrupy snapshots of Markdown + serialized IR for every real fixture (FEAT-004).

For each saved HTML fixture (FEAT-003) this builds a deterministic SourceInfo
(fixed ``fetched_at``, sha256 ``content_hash``), runs :func:`extract_html`, and
snapshots BOTH:

- the Markdown produced by :func:`to_markdown` (always from the IR, SPEC §4), and
- the serialized IR (``Document.model_dump(mode="json")``).

``mode="json"`` serializes the one datetime field (``source.fetched_at``) to a
stable ISO string; because that datetime is the fixed constant from
``_fixtures.py`` and the extractor is otherwise deterministic (no timestamps, no
unordered-set iteration — SPEC §4 / CLAUDE.md), re-runs are byte-stable. The
``__snapshots__`` directory is checked in.
"""

from __future__ import annotations

import pytest
from syrupy.assertion import SnapshotAssertion

from app.extract.html import extract_html
from app.extract.to_markdown import to_markdown
from tests.unit.extract._fixtures import fixture_ids, read_fixture, source_for


@pytest.mark.parametrize("stem", fixture_ids())
def test_fixture_markdown_snapshot(stem: str, snapshot: SnapshotAssertion) -> None:
    body = read_fixture(stem)
    doc = extract_html(body, source_for(stem, body))
    assert to_markdown(doc) == snapshot


@pytest.mark.parametrize("stem", fixture_ids())
def test_fixture_ir_snapshot(stem: str, snapshot: SnapshotAssertion) -> None:
    body = read_fixture(stem)
    doc = extract_html(body, source_for(stem, body))
    assert doc.model_dump(mode="json") == snapshot
