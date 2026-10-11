"""SQLAlchemy 2 models for the persistence layer — M5 (SPEC §7).

Two tables back the resolve/get-document endpoints:

- ``sources`` — one row per normalized URL. ``id`` holds the ``doc_`` document
  id. ``current_version_id`` points at the latest persisted version; the FK is
  created with ``use_alter=True`` because ``sources`` and ``document_versions``
  reference each other (a cycle Alembic/DDL must break with a deferred
  ``ALTER TABLE``).
- ``document_versions`` — one row per distinct content hash of a source. A
  ``(source_id, content_hash)`` unique constraint enforces dedup at the schema
  level; the resolve handler never creates a second version for an unchanged
  body.

``warnings`` is stored as JSONB (Postgres). ``status`` is a textual enum that is
``'ready'`` for the MVP (DECISIONS.md 2026-10-11); a version is only persisted
after a successful extraction. Chunks/api_keys/usage_events belong to M6/M9 and
are intentionally absent here.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, UniqueConstraint, func
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base shared by all M5 models."""


class Source(Base):
    """A fetched source, keyed by its normalized URL (SPEC §7)."""

    __tablename__ = "sources"

    # Holds the doc_... document_id (make_document_id of the normalized URL).
    id: Mapped[str] = mapped_column(primary_key=True)
    url_normalized: Mapped[str] = mapped_column(unique=True, index=True)
    domain: Mapped[str] = mapped_column(index=True)
    current_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("document_versions.id", use_alter=True),
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class DocumentVersion(Base):
    """One version of one source, keyed by its ver_... id (SPEC §7)."""

    __tablename__ = "document_versions"
    __table_args__ = (UniqueConstraint("source_id", "content_hash"),)

    # Holds the ver_... version_id (make_version_id of the content hash).
    id: Mapped[str] = mapped_column(primary_key=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), index=True)
    content_hash: Mapped[str] = mapped_column(index=True)
    fetched_at: Mapped[datetime] = mapped_column()
    etag: Mapped[str | None] = mapped_column()
    last_modified: Mapped[str | None] = mapped_column()
    render_mode: Mapped[str] = mapped_column()
    raw_blob_key: Mapped[str] = mapped_column()
    ir_blob_key: Mapped[str] = mapped_column()
    status: Mapped[str] = mapped_column(default="ready")
    warnings: Mapped[list[dict[str, object]]] = mapped_column(postgresql.JSONB)
