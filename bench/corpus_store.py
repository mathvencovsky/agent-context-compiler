"""Frozen-corpus manifest model and read-only store (SPEC §10.1).

SPEC §10.1 requires the benchmark corpus to be *frozen*: ``bench/snapshot.py``
fetches every site URL once and writes the bytes under ``bench/corpus/`` with a
``manifest.json`` describing each entry. Every strategy then reads from this
snapshot — never the live web — so a run is reproducible and offline.

This module is the read side of that contract:

* :class:`ManifestEntry` / :class:`Manifest` are the Pydantic model of
  ``manifest.json`` (contract in ``.agents/tasks/m2-phase-a/m2a-plan.md``).
* :func:`load_manifest` parses ``<corpus_dir>/manifest.json``.
* :class:`CorpusStore` resolves raw bytes, rendered HTML and the content type of
  a URL from the frozen files, keyed by the URL recorded in the manifest.

No network is performed here, and ``httpx`` is not imported — the store only
reads files that ``snapshot.py`` already wrote.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel

#: Status of a manifest entry: ``"ok"`` when the fetch (and optional render)
#: succeeded, ``"skipped"`` when the URL was dropped (robots/fetch error) with a
#: human ``reason`` recorded instead of bytes.
EntryStatus = Literal["ok", "skipped"]

#: File name of the manifest written at the root of a corpus directory.
MANIFEST_FILENAME = "manifest.json"


class ManifestEntry(BaseModel):
    """One frozen URL in the corpus (SPEC §10.1 / m2a-plan.md contract)."""

    site: str
    site_key: str
    rich_categories: list[str]
    url: str
    final_url: str
    status: EntryStatus
    reason: str | None
    content_type: str
    content_hash: str
    fetched_at: str
    raw_path: str
    rendered_path: str | None


class Manifest(BaseModel):
    """The whole ``manifest.json``: a creation timestamp and the entries."""

    created_at: str
    entries: list[ManifestEntry]


def load_manifest(corpus_dir: str | Path) -> Manifest:
    """Load and validate ``<corpus_dir>/manifest.json`` into a :class:`Manifest`.

    Raises :class:`FileNotFoundError` if the manifest is missing (the corpus was
    never snapshotted) and ``pydantic.ValidationError`` if it does not match the
    contract — both failing loudly rather than returning a partial corpus.
    """
    corpus_dir = Path(corpus_dir)
    manifest_path = corpus_dir / MANIFEST_FILENAME
    raw = manifest_path.read_text(encoding="utf-8")
    return Manifest.model_validate_json(raw)


class CorpusStore:
    """Read-only accessor over a frozen corpus directory (SPEC §10.1).

    Construction loads the manifest and indexes entries by ``url`` so runners can
    ask for a URL's bytes, rendered HTML or content type without touching the
    network. ``skipped`` entries hold no bytes; asking for their content raises.
    """

    def __init__(self, corpus_dir: str | Path) -> None:
        self.corpus_dir = Path(corpus_dir)
        self.manifest = load_manifest(self.corpus_dir)
        # Requested URLs win; a redirect's final URL also resolves (SPEC §10.6
        # lists cited pages after redirect).
        self._by_url: dict[str, ManifestEntry] = {
            **{e.final_url: e for e in self.manifest.entries if e.status == "ok"},
            **{e.url: e for e in self.manifest.entries},
        }

    def entries(self) -> list[ManifestEntry]:
        """Return the manifest entries in snapshot order."""
        return list(self.manifest.entries)

    def _entry(self, url: str) -> ManifestEntry:
        """Return the entry for ``url`` or raise a clear ``KeyError``."""
        try:
            return self._by_url[url]
        except KeyError as exc:
            raise KeyError(f"url not in corpus manifest: {url!r}") from exc

    def _require_ok(self, entry: ManifestEntry) -> None:
        """Raise if ``entry`` holds no bytes (it was skipped at snapshot time)."""
        if entry.status != "ok":
            raise ValueError(
                f"corpus entry for {entry.url!r} is {entry.status!r} "
                f"(reason: {entry.reason!r}); no bytes stored"
            )

    def raw_bytes(self, url: str) -> bytes:
        """Return the raw fetched body stored for ``url``."""
        entry = self._entry(url)
        self._require_ok(entry)
        return (self.corpus_dir / entry.raw_path).read_bytes()

    def rendered_html(self, url: str) -> str | None:
        """Return the browser-rendered HTML for ``url``, or ``None`` if not rendered."""
        entry = self._entry(url)
        self._require_ok(entry)
        if entry.rendered_path is None:
            return None
        return (self.corpus_dir / entry.rendered_path).read_text(encoding="utf-8")

    def content_type(self, url: str) -> str:
        """Return the recorded media type (no charset) for ``url``."""
        return self._entry(url).content_type
