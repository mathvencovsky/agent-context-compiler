"""Usage-event write path + aggregation — M9 (SPEC §7/§8 'GET /v1/usage').

:class:`UsageEventRepo` is session-scoped (same pattern as ``DocumentRepo`` /
``ApiKeyRepo``). :meth:`record` persists ONE :class:`~app.store.models.UsageEvent`
per authenticated /v1 request (written by the usage middleware AFTER the
response). :meth:`aggregate` rolls a key's events up BY (UTC day, endpoint) with
request count, delivered tokens (``units.output_tokens``), whole-page tokens
(``units.page_tokens``), renders (``units.renders``) and p50/p95 latency. Key
``None`` means the keyless local-use rows (DECISIONS 2026-11-02).

The percentiles use the NEAREST-RANK method over the matching rows' integer
``latency_ms`` (quantiles computed in Python, not SQL — simpler and trivially
testable offline against a known latency set). The ``units`` JSONB is read
defensively: a missing/non-numeric counter contributes 0 so a malformed row
never breaks the aggregate.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from math import ceil

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.store.models import UsageEvent
from app.usage.keys import make_usage_event_id


@dataclass(frozen=True)
class UsageBucket:
    """One (day, endpoint) rollup of a key's usage."""

    day: str
    endpoint: str
    requests: int
    tokens_delivered: int
    page_tokens: int
    renders: int
    latency_p50_ms: int
    latency_p95_ms: int


class UsageEventRepo:
    """Session-scoped repository that writes and aggregates usage events."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def record(
        self,
        *,
        api_key_id: str | None,
        endpoint: str,
        ts: datetime,
        status_code: int,
        latency_ms: int,
        units: dict[str, object],
    ) -> UsageEvent:
        """Persist one usage event and return it."""
        event = UsageEvent(
            id=make_usage_event_id(),
            api_key_id=api_key_id,
            endpoint=endpoint,
            ts=ts,
            status_code=status_code,
            latency_ms=latency_ms,
            units=units,
        )
        self._session.add(event)
        self._session.flush()
        return event

    def recent(self, limit: int) -> list[UsageEvent]:
        """The newest keyless local calls, leaving out reads of the usage itself."""
        stmt = (
            select(UsageEvent)
            .where(
                UsageEvent.api_key_id.is_(None),
                UsageEvent.endpoint != "/v1/usage",
            )
            .order_by(UsageEvent.ts.desc())
            .limit(limit)
        )
        return list(self._session.scalars(stmt).all())

    def aggregate(self, api_key_id: str | None) -> list[UsageBucket]:
        """Roll a key's events (``None``: keyless local use) up by (day, endpoint).

        Returns one :class:`UsageBucket` per (day, endpoint) pair, sorted by day
        then endpoint, with request count, summed delivered tokens
        (``units.output_tokens``), summed whole-page tokens
        (``units.page_tokens``), summed renders (``units.renders``) and
        nearest-rank p50/p95 of the group's ``latency_ms``.
        """
        key = (
            UsageEvent.api_key_id.is_(None)
            if api_key_id is None
            else UsageEvent.api_key_id == api_key_id
        )
        stmt = select(UsageEvent).where(key)
        events = self._session.scalars(stmt).all()

        grouped: dict[tuple[str, str], list[UsageEvent]] = defaultdict(list)
        for event in events:
            day = _utc_day(event.ts)
            grouped[(day, event.endpoint)].append(event)

        buckets: list[UsageBucket] = []
        for (day, endpoint), rows in grouped.items():
            latencies = [int(row.latency_ms) for row in rows]
            buckets.append(
                UsageBucket(
                    day=day,
                    endpoint=endpoint,
                    requests=len(rows),
                    tokens_delivered=sum(
                        _unit_int(row.units, "output_tokens") for row in rows
                    ),
                    page_tokens=sum(
                        _unit_int(row.units, "page_tokens") for row in rows
                    ),
                    renders=sum(_unit_int(row.units, "renders") for row in rows),
                    latency_p50_ms=_percentile(latencies, 50),
                    latency_p95_ms=_percentile(latencies, 95),
                )
            )

        buckets.sort(key=lambda b: (b.day, b.endpoint))
        return buckets


def _utc_day(ts: datetime) -> str:
    """Return the ISO ``YYYY-MM-DD`` of a timestamp (naive assumed UTC)."""
    return ts.date().isoformat()


def _unit_int(units: object, name: str) -> int:
    """Read an integer counter from a ``units`` mapping (0 if absent/invalid)."""
    if not isinstance(units, dict):
        return 0
    value = units.get(name)
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return 0


def panel_row(event: UsageEvent) -> dict[str, object]:
    """One call for the local panel; ``units`` is read defensively."""
    units = event.units if isinstance(event.units, dict) else {}
    hosts = units.get("hosts")
    client = units.get("client")
    return {
        "ts": event.ts.replace(tzinfo=event.ts.tzinfo or UTC).isoformat(),
        "endpoint": event.endpoint,
        "status": event.status_code,
        "latency_ms": event.latency_ms,
        "output_tokens": _unit_int(units, "output_tokens"),
        "page_tokens": _unit_int(units, "page_tokens"),
        "failed": _unit_int(units, "failed"),
        "hosts": [h for h in hosts if isinstance(h, str)]
        if isinstance(hosts, list)
        else [],
        "client": client if isinstance(client, str) else None,
    }


def _percentile(values: list[int], pct: int) -> int:
    """Nearest-rank percentile of ``values`` (0 for an empty list).

    Nearest-rank: rank = ceil(pct/100 * N), 1-indexed into the sorted values.
    """
    if not values:
        return 0
    ordered = sorted(values)
    rank = max(1, ceil(pct / 100 * len(ordered)))
    return ordered[rank - 1]


__all__ = ["UsageBucket", "UsageEventRepo", "panel_row"]
