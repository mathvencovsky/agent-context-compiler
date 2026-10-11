"""Shared helpers for the fixture-wide acceptance tests (FEAT-004).

These helpers load the real HTML snapshots saved under ``tests/fixtures/html/``
(FEAT-003) and build a deterministic :class:`~app.extract.ir.SourceInfo` for
them. Determinism is the whole point of the acceptance suite: the ``fetched_at``
is a FIXED constant so syrupy snapshots are stable across runs, and the
``content_hash`` is the sha256 of the file bytes (the same contract the real
fetcher would produce), so ``version_id`` is reproducible.

This module has no ``test_`` functions and is not collected by pytest; it is
imported by the ``test_fixtures_*`` modules.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

from app.extract.ir import SourceInfo

# Directory holding the real saved HTML snapshots (FEAT-003).
FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "html"

# A FIXED fetched_at so snapshots of the serialized IR are deterministic across
# runs (SourceInfo.fetched_at would otherwise be the only moving field).
FIXED_FETCHED_AT = datetime(2026, 10, 11, 0, 0, 0, tzinfo=UTC)

# Stable base used to derive each fixture's url/final_url. The real source URLs
# live in SOURCES.md; here we only need a stable, deterministic value per file.
_BASE_URL = "https://fixtures.nina.test/html"


def fixture_paths() -> list[Path]:
    """Return every saved HTML fixture, sorted by name for determinism."""
    return sorted(FIXTURES_DIR.glob("*.html"))


def fixture_ids() -> list[str]:
    """Return the fixture stems (filename without suffix), sorted."""
    return [path.stem for path in fixture_paths()]


def read_fixture(stem: str) -> bytes:
    """Return the raw bytes of the fixture named ``stem``."""
    return (FIXTURES_DIR / f"{stem}.html").read_bytes()


def source_for(stem: str, body: bytes) -> SourceInfo:
    """Build a deterministic SourceInfo for a fixture's bytes.

    ``url``/``final_url`` are derived from the fixture name (stable, not the real
    source URL — the real URLs are recorded in SOURCES.md). ``content_hash`` is
    the sha256 of ``body`` so ``version_id`` is reproducible, and ``fetched_at``
    is the fixed constant above so snapshots never drift.
    """
    final_url = f"{_BASE_URL}/{stem}.html"
    return SourceInfo(
        url=final_url,
        final_url=final_url,
        content_type="text/html; charset=utf-8",
        fetched_at=FIXED_FETCHED_AT,
        etag=None,
        last_modified=None,
        content_hash=hashlib.sha256(body).hexdigest(),
        render_mode="http",
    )
