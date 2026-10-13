"""Integration test: the 0004 migration shaped api_keys/usage_events — M9.

Runs against the real test Postgres (migrated to head by the session ``engine``
fixture). It inspects the live schema so a drift between the migration and the
ORM models is caught, and round-trips a key + a usage event to prove the FK and
JSONB columns behave.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import inspect
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.store.models import ApiKey, UsageEvent
from app.usage.keys import ApiKeyRepo, make_usage_event_id

pytestmark = pytest.mark.integration


def test_tables_and_indexes_exist(engine: Engine) -> None:
    """0004 created both tables with the expected columns and indexes."""
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    assert {"api_keys", "usage_events"} <= tables

    api_cols = {c["name"] for c in inspector.get_columns("api_keys")}
    assert api_cols == {
        "id",
        "key_hash",
        "name",
        "created_at",
        "revoked_at",
        "limits",
    }

    usage_cols = {c["name"] for c in inspector.get_columns("usage_events")}
    assert usage_cols == {
        "id",
        "api_key_id",
        "endpoint",
        "ts",
        "status_code",
        "latency_ms",
        "units",
    }

    index_names = {ix["name"] for ix in inspector.get_indexes("usage_events")}
    assert "ix_usage_events_api_key_ts" in index_names
    assert "ix_usage_events_api_key_id" in index_names

    fks = inspector.get_foreign_keys("usage_events")
    assert any(fk["referred_table"] == "api_keys" for fk in fks)


def test_key_and_usage_event_round_trip(
    engine: Engine, session_factory: sessionmaker[Session]
) -> None:
    """A key persists only its hash and a usage event links back via the FK."""
    with engine.begin() as conn:
        from sqlalchemy import text

        conn.execute(text("DELETE FROM usage_events"))
        conn.execute(text("DELETE FROM api_keys"))

    session: Session = session_factory()
    try:
        api_key, plaintext = ApiKeyRepo(session).create("schema-test", {"rpm": 10})
        event = UsageEvent(
            id=make_usage_event_id(),
            api_key_id=api_key.id,
            endpoint="/v1/documents/resolve",
            ts=datetime.now(UTC),
            status_code=200,
            latency_ms=12,
            units={"documents": 1},
        )
        session.add(event)
        session.commit()

        reloaded = session.get(ApiKey, api_key.id)
        assert reloaded is not None
        assert reloaded.key_hash != plaintext
        assert reloaded.limits == {"rpm": 10}

        reloaded_event = session.get(UsageEvent, event.id)
        assert reloaded_event is not None
        assert reloaded_event.api_key_id == api_key.id
        assert reloaded_event.units == {"documents": 1}
    finally:
        with engine.begin() as conn:
            from sqlalchemy import text

            conn.execute(text("DELETE FROM usage_events"))
            conn.execute(text("DELETE FROM api_keys"))
        session.close()
