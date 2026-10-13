"""Offline fixtures for the M9 key/admin unit tests.

A throwaway in-memory SQLite engine backs ``ApiKeyRepo`` so the tests run with
NO services and NO network (CLAUDE.md). Only the ``api_keys`` table is created
(the JSONB columns fall back to plain JSON on SQLite via the model variant); the
real database is always Postgres.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.store.models import ApiKey


@pytest.fixture
def session_factory() -> Iterator[sessionmaker[Session]]:
    engine = create_engine("sqlite://")
    ApiKey.__table__.create(engine)
    try:
        yield sessionmaker(bind=engine)
    finally:
        engine.dispose()
