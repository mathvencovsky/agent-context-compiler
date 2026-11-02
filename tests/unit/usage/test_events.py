"""Offline unit tests for UsageEventRepo.record + aggregate — M9.

An in-memory SQLite engine backs the repo (NO services, NO network — CLAUDE.md).
The ``api_keys``/``usage_events`` JSONB columns fall back to plain JSON on SQLite
via the model variant; SQLite does not enforce the FK, so a fixed ``api_key_id``
literal is used without inserting a parent row.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.store.models import ApiKey, UsageEvent
from app.usage.events import UsageEventRepo

_KEY = "key_test"


@pytest.fixture
def session_factory() -> Iterator[sessionmaker[Session]]:
    engine = create_engine("sqlite://")
    ApiKey.__table__.create(engine)
    UsageEvent.__table__.create(engine)
    try:
        yield sessionmaker(bind=engine)
    finally:
        engine.dispose()


def _record(
    repo: UsageEventRepo,
    *,
    endpoint: str,
    ts: datetime,
    status_code: int = 200,
    latency_ms: int,
    units: dict[str, object] | None = None,
) -> None:
    repo.record(
        api_key_id=_KEY,
        endpoint=endpoint,
        ts=ts,
        status_code=status_code,
        latency_ms=latency_ms,
        units=units or {},
    )


def test_record_persists_an_event(session_factory: sessionmaker[Session]) -> None:
    session = session_factory()
    try:
        repo = UsageEventRepo(session)
        event = repo.record(
            api_key_id=_KEY,
            endpoint="/v1/documents/resolve",
            ts=datetime(2026, 10, 14, 10, 0, tzinfo=UTC),
            status_code=200,
            latency_ms=42,
            units={"renders": 1},
        )
        session.commit()
        assert event.id.startswith("evt_")
        assert event.endpoint == "/v1/documents/resolve"
    finally:
        session.close()


def test_aggregate_groups_by_day_and_endpoint(
    session_factory: sessionmaker[Session],
) -> None:
    session = session_factory()
    try:
        repo = UsageEventRepo(session)
        day1 = datetime(2026, 10, 14, 9, 0, tzinfo=UTC)
        day2 = datetime(2026, 10, 15, 9, 0, tzinfo=UTC)

        # Day 1: two resolves (one render) + one assemble (1200 tokens).
        _record(
            repo,
            endpoint="/v1/documents/resolve",
            ts=day1,
            latency_ms=10,
            units={"renders": 1},
        )
        _record(
            repo,
            endpoint="/v1/documents/resolve",
            ts=day1,
            latency_ms=30,
            units={"renders": 0},
        )
        _record(
            repo,
            endpoint="/v1/context/assemble",
            ts=day1,
            latency_ms=100,
            units={"output_tokens": 1200, "renders": 0},
        )
        # Day 2: one resolve.
        _record(
            repo,
            endpoint="/v1/documents/resolve",
            ts=day2,
            latency_ms=50,
            units={"renders": 0},
        )
        session.commit()

        buckets = repo.aggregate(_KEY)
        # Sorted by (day, endpoint): day1 assemble, day1 resolve, day2 resolve.
        assert [(b.day, b.endpoint) for b in buckets] == [
            ("2026-10-14", "/v1/context/assemble"),
            ("2026-10-14", "/v1/documents/resolve"),
            ("2026-10-15", "/v1/documents/resolve"),
        ]

        assemble_bucket = buckets[0]
        assert assemble_bucket.requests == 1
        assert assemble_bucket.tokens_delivered == 1200
        assert assemble_bucket.renders == 0

        resolve_bucket = buckets[1]
        assert resolve_bucket.requests == 2
        assert resolve_bucket.renders == 1
        assert resolve_bucket.tokens_delivered == 0
    finally:
        session.close()


def test_aggregate_percentiles_nearest_rank(
    session_factory: sessionmaker[Session],
) -> None:
    session = session_factory()
    try:
        repo = UsageEventRepo(session)
        ts = datetime(2026, 10, 14, 9, 0, tzinfo=UTC)
        # Latencies 10,20,30,40,50 -> nearest-rank p50 = rank ceil(0.5*5)=3 ->
        # 30; p95 = rank ceil(0.95*5)=5 -> 50.
        for latency in (10, 20, 30, 40, 50):
            _record(repo, endpoint="/v1/documents/resolve", ts=ts, latency_ms=latency)
        session.commit()

        bucket = repo.aggregate(_KEY)[0]
        assert bucket.latency_p50_ms == 30
        assert bucket.latency_p95_ms == 50
    finally:
        session.close()


def test_aggregate_keyless_local_rows_apart_with_page_tokens(
    session_factory: sessionmaker[Session],
) -> None:
    session = session_factory()
    try:
        repo = UsageEventRepo(session)
        ts = datetime(2026, 11, 2, 10, 0, tzinfo=UTC)
        for key, units in (
            (None, {"output_tokens": 900, "page_tokens": 5000}),
            (None, {"output_tokens": 100, "page_tokens": 1000}),
            (_KEY, {"output_tokens": 7, "page_tokens": 70}),
        ):
            repo.record(
                api_key_id=key,
                endpoint="/v1/context/assemble",
                ts=ts,
                status_code=200,
                latency_ms=50,
                units=units,
            )
        session.commit()
        (local,) = repo.aggregate(None)
        assert (local.requests, local.tokens_delivered, local.page_tokens) == (
            2,
            1000,
            6000,
        )
        (keyed,) = repo.aggregate(_KEY)
        assert (keyed.requests, keyed.tokens_delivered, keyed.page_tokens) == (1, 7, 70)
    finally:
        session.close()


def test_aggregate_empty_for_unknown_key(
    session_factory: sessionmaker[Session],
) -> None:
    session = session_factory()
    try:
        assert UsageEventRepo(session).aggregate("key_unknown") == []
    finally:
        session.close()
