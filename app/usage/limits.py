"""In-memory token-bucket rate limiter + daily render quota — M9 (SPEC §8).

The limiter is PROCESS-LOCAL: one :class:`TokenBucketLimiter` instance holds a
``dict`` of per-key bucket/quota state guarded by a single
:class:`threading.Lock`. This is the human decision for the MVP (NOT Postgres):
it is simple and allocation-free on the hot path, but it does NOT scale
horizontally — each worker process keeps its OWN buckets, so a key's effective
limit is multiplied by the worker count. The MVP runs a single worker, which
makes the per-process bucket the whole picture; a multi-worker/multi-host
deployment would need a shared store (Redis) instead. Documented so FEAT-003/004
and ops know the boundary.

Two independent limits per key:

* a **token bucket** (burst ``capacity`` tokens, refilled at ``refill_per_sec``)
  — every /v1 request costs one token; an empty bucket yields
  :class:`RateLimited` with the seconds until the next token (always > 0).
* a **daily render quota** — browser renders counted per ``(key, UTC day)``; the
  ``N+1``-th render in a day yields :class:`RateLimited` with the seconds until
  the next UTC midnight.

The clock is injected (``time_fn`` monotonic seconds for the bucket; ``now_fn``
wall-clock UTC for the quota day) so tests advance time deterministically with
NO sleeping. Per-key overrides are read from the key's ``limits`` JSONB
(``rate_capacity`` / ``rate_refill_per_sec`` / ``render_daily_quota``), falling
back to the :class:`~app.config.Settings` defaults.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.config import Settings


@dataclass(frozen=True)
class Allowed:
    """The request is within the limit and may proceed."""


@dataclass(frozen=True)
class RateLimited:
    """The limit was exceeded; retry after ``retry_after_seconds`` (> 0)."""

    retry_after_seconds: float


#: Result of a limiter check: either allowed or rate-limited.
LimitResult = Allowed | RateLimited


def _as_float(value: object, fallback: float) -> float:
    """Coerce a per-key JSONB override to float, else return the fallback."""
    if isinstance(value, bool):
        return fallback
    if isinstance(value, (int, float)):
        return float(value)
    return fallback


def _as_int(value: object, fallback: int) -> int:
    """Coerce a per-key JSONB override to int, else return the fallback."""
    if isinstance(value, bool):
        return fallback
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return fallback


@dataclass
class _Bucket:
    """Token-bucket state for one key."""

    tokens: float
    last_refill: float


@dataclass
class _RenderQuota:
    """Daily render counter for one key, scoped to a UTC day."""

    day: str
    count: int


def _default_now() -> datetime:
    """Return the current wall-clock time in UTC."""
    return datetime.now(UTC)


class TokenBucketLimiter:
    """Process-local, thread-safe per-key token bucket + daily render quota.

    ``time_fn`` returns monotonic seconds for the bucket refill; ``now_fn``
    returns wall-clock UTC for the render-quota day boundary. Both default to
    the real clocks; tests inject controllable ones so a fresh limiter per test
    never leaks state (the limiter is an injectable dependency seam).
    """

    def __init__(
        self,
        settings: Settings,
        *,
        time_fn: Callable[[], float] | None = None,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self.settings = settings
        self.time_fn: Callable[[], float] = time_fn or time.monotonic
        self.now_fn: Callable[[], datetime] = now_fn or _default_now
        self._lock = threading.Lock()
        self._buckets: dict[str, _Bucket] = {}
        self._quotas: dict[str, _RenderQuota] = {}

    # ----------------------------------------------------------------- bucket
    def acquire(
        self, key: str, *, cost: float = 1.0, limits: dict[str, object] | None = None
    ) -> LimitResult:
        """Try to spend ``cost`` tokens from ``key``'s bucket.

        Returns :class:`Allowed` when enough tokens were available (and spends
        them), else :class:`RateLimited` with the seconds until ``cost`` tokens
        will have refilled (always > 0). Per-key overrides come from ``limits``.
        """
        overrides = limits or {}
        capacity = _as_float(
            overrides.get("rate_capacity"), float(self.settings.rate_limit_capacity)
        )
        refill = _as_float(
            overrides.get("rate_refill_per_sec"),
            self.settings.rate_limit_refill_per_sec,
        )
        now = self.time_fn()
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = _Bucket(tokens=capacity, last_refill=now)
                self._buckets[key] = bucket
            else:
                elapsed = max(0.0, now - bucket.last_refill)
                bucket.tokens = min(capacity, bucket.tokens + elapsed * refill)
                bucket.last_refill = now

            if bucket.tokens >= cost:
                bucket.tokens -= cost
                return Allowed()

            missing = cost - bucket.tokens
            retry_after = missing / refill if refill > 0 else float("inf")
            return RateLimited(retry_after_seconds=retry_after)

    # ------------------------------------------------------------ render quota
    def try_render(
        self, key: str, *, limits: dict[str, object] | None = None
    ) -> LimitResult:
        """Count one render against ``key``'s daily quota.

        Returns :class:`Allowed` (and increments the counter) while the key is
        under its per-UTC-day quota, else :class:`RateLimited` with the seconds
        until the next UTC midnight. Per-key override: ``render_daily_quota``.
        """
        overrides = limits or {}
        quota = _as_int(
            overrides.get("render_daily_quota"), self.settings.render_daily_quota
        )
        now = self.now_fn()
        day = now.date().isoformat()
        with self._lock:
            state = self._quotas.get(key)
            if state is None or state.day != day:
                state = _RenderQuota(day=day, count=0)
                self._quotas[key] = state

            if state.count < quota:
                state.count += 1
                return Allowed()

            return RateLimited(retry_after_seconds=_seconds_until_utc_midnight(now))


def _seconds_until_utc_midnight(now: datetime) -> float:
    """Seconds from ``now`` (UTC) to the next UTC midnight (always > 0)."""
    moment = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
    next_midnight = (moment + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return max(1.0, (next_midnight - moment).total_seconds())


__all__ = [
    "Allowed",
    "LimitResult",
    "RateLimited",
    "TokenBucketLimiter",
]
