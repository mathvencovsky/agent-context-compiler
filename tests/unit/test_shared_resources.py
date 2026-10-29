"""Expensive clients are built once per process (M10b efficiency).

The API seams used to build a SQLAlchemy engine (a new connection pool) and a
boto3 client on every request. Building them never touches the network, so
these checks need no services.
"""

from __future__ import annotations

from app.api import auth, documents, usage
from app.config import Settings
from app.store.repo import shared_engine


def test_session_factories_share_one_engine() -> None:
    engines = {
        documents.provide_session_factory().kw["bind"],
        documents.provide_session_factory().kw["bind"],
        auth.provide_auth_session_factory().kw["bind"],
        usage.provide_usage_session_factory().kw["bind"],
    }
    assert len(engines) == 1
    (engine,) = engines
    assert engine.pool._pre_ping is True


def test_shared_engine_is_per_database_url() -> None:
    a = Settings(postgres_db="nina_a")
    b = Settings(postgres_db="nina_b")
    assert shared_engine(a) is shared_engine(Settings(postgres_db="nina_a"))
    assert shared_engine(a) is not shared_engine(b)


def test_blob_store_is_one_per_process() -> None:
    assert documents.provide_blob_store() is documents.provide_blob_store()
