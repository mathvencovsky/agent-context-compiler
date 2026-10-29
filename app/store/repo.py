"""Data-access layer for sources/document_versions — M5.

Synchronous SQLAlchemy (the resolve path reuses the audited synchronous
fetcher; FastAPI runs ``def`` handlers in a threadpool — DECISIONS.md
2026-10-11). ``DocumentRepo`` wraps one ``Session`` and exposes the exact
operations the resolve handler needs. Dedup is enforced by the caller: no new
version is created when the source already holds a version with the same
content hash AND extractor version (the version id encodes both).
"""

from __future__ import annotations

from datetime import datetime
from urllib.parse import urlsplit

from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.store.models import DocumentVersion, Source


def make_engine(settings: Settings) -> Engine:
    """Create a synchronous SQLAlchemy engine from the settings DSN."""
    return create_engine(settings.database_url)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Create a session factory bound to ``engine``."""
    return sessionmaker(bind=engine)


class DocumentRepo:
    """Session-scoped repository for sources and their versions."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_source_by_url(self, url_normalized: str) -> Source | None:
        """Return the source for a normalized URL, or None."""
        stmt = select(Source).where(Source.url_normalized == url_normalized)
        return self._session.scalars(stmt).one_or_none()

    def get_source_by_document_id(self, document_id: str) -> Source | None:
        """Return the source whose id is ``document_id`` (the doc_ id), or None."""
        return self._session.get(Source, document_id)

    def get_or_create_source(
        self, url_normalized: str, domain: str, document_id: str
    ) -> Source:
        """Return the existing source for the URL, creating one if absent."""
        existing = self.get_source_by_url(url_normalized)
        if existing is not None:
            return existing
        source = Source(
            id=document_id,
            url_normalized=url_normalized,
            domain=domain,
        )
        self._session.add(source)
        self._session.flush()
        return source

    def get_current_version(self, source: Source) -> DocumentVersion | None:
        """Return the source's current version, or None."""
        if source.current_version_id is None:
            return None
        return self.get_version(source.current_version_id)

    def get_version(self, version_id: str) -> DocumentVersion | None:
        """Return a version by id, or None."""
        return self._session.get(DocumentVersion, version_id)

    def get_version_by_hash(
        self, source: Source, content_hash: str
    ) -> DocumentVersion | None:
        """Return the source's version with the given content hash, or None."""
        stmt = select(DocumentVersion).where(
            DocumentVersion.source_id == source.id,
            DocumentVersion.content_hash == content_hash,
        )
        return self._session.scalars(stmt).one_or_none()

    def create_version(
        self,
        *,
        source: Source,
        version_id: str,
        content_hash: str,
        fetched_at: datetime,
        etag: str | None,
        last_modified: str | None,
        render_mode: str,
        raw_blob_key: str,
        ir_blob_key: str,
        warnings: list[dict[str, object]],
        extractor_version: str,
        content_type_header: str | None,
        link_header: str | None,
    ) -> DocumentVersion:
        """Insert a version and set it as the source's current version."""
        version = DocumentVersion(
            id=version_id,
            source_id=source.id,
            content_hash=content_hash,
            fetched_at=fetched_at,
            etag=etag,
            last_modified=last_modified,
            render_mode=render_mode,
            raw_blob_key=raw_blob_key,
            ir_blob_key=ir_blob_key,
            status="ready",
            warnings=warnings,
            extractor_version=extractor_version,
            content_type_header=content_type_header,
            link_header=link_header,
        )
        self._session.add(version)
        self._session.flush()
        source.current_version_id = version.id
        self._session.flush()
        return version

    def touch_fetched_at(self, version: DocumentVersion, when: datetime) -> None:
        """Update a version's ``fetched_at`` timestamp."""
        version.fetched_at = when
        self._session.flush()

    def set_current_version(self, source: Source, version: DocumentVersion) -> None:
        """Point a source's current version at ``version``."""
        source.current_version_id = version.id
        self._session.flush()


def domain_from_url(url_normalized: str) -> str:
    """Return the host portion of a normalized URL (empty string if absent)."""
    return urlsplit(url_normalized).hostname or ""
