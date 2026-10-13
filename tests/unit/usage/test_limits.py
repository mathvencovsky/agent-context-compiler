"""Offline unit tests for the in-memory token bucket + render quota — M9.

The clock is injected (monotonic seconds for the bucket, wall-clock UTC for the
render quota) so time advances deterministically with NO sleeping and no
network/services (CLAUDE.md).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.config import Settings
from app.usage.limits import Allowed, RateLimited, TokenBucketLimiter


class FakeMonotonic:
    """A controllable monotonic clock (seconds)."""

    def __init__(self, start: float = 0.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class FakeWallClock:
    """A controllable wall-clock returning aware UTC datetimes."""

    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: float) -> None:
        self.now = self.now + timedelta(**kwargs)


@pytest.fixture
def settings() -> Settings:
    """Settings with small, test-friendly limits (never reads a real .env)."""
    return Settings(
        rate_limit_capacity=3,
        rate_limit_refill_per_sec=1.0,
        render_daily_quota=2,
    )


def test_bucket_allows_up_to_capacity_then_rejects(settings: Settings) -> None:
    clock = FakeMonotonic()
    limiter = TokenBucketLimiter(settings, time_fn=clock)

    # Capacity is 3: the first three requests pass.
    for _ in range(3):
        assert isinstance(limiter.acquire("key_a"), Allowed)

    # The fourth is rate-limited with a positive retry_after.
    result = limiter.acquire("key_a")
    assert isinstance(result, RateLimited)
    assert result.retry_after_seconds > 0


def test_bucket_refills_after_time_passes(settings: Settings) -> None:
    clock = FakeMonotonic()
    limiter = TokenBucketLimiter(settings, time_fn=clock)

    for _ in range(3):
        limiter.acquire("key_a")
    assert isinstance(limiter.acquire("key_a"), RateLimited)

    # Refill is 1 token/sec; after 1s exactly one token is available again.
    clock.advance(1.0)
    assert isinstance(limiter.acquire("key_a"), Allowed)
    assert isinstance(limiter.acquire("key_a"), RateLimited)


def test_retry_after_matches_missing_tokens(settings: Settings) -> None:
    clock = FakeMonotonic()
    limiter = TokenBucketLimiter(settings, time_fn=clock)
    for _ in range(3):
        limiter.acquire("key_a")

    result = limiter.acquire("key_a", cost=2.0)
    assert isinstance(result, RateLimited)
    # Need 2 tokens, bucket empty, refill 1/sec -> ~2s.
    assert result.retry_after_seconds == pytest.approx(2.0)


def test_buckets_are_isolated_per_key(settings: Settings) -> None:
    clock = FakeMonotonic()
    limiter = TokenBucketLimiter(settings, time_fn=clock)

    for _ in range(3):
        limiter.acquire("key_a")
    assert isinstance(limiter.acquire("key_a"), RateLimited)

    # A different key has its own fresh bucket.
    assert isinstance(limiter.acquire("key_b"), Allowed)


def test_per_key_capacity_override(settings: Settings) -> None:
    clock = FakeMonotonic()
    limiter = TokenBucketLimiter(settings, time_fn=clock)

    overrides = {"rate_capacity": 5, "rate_refill_per_sec": 0.5}
    for _ in range(5):
        assert isinstance(limiter.acquire("key_a", limits=overrides), Allowed)
    assert isinstance(limiter.acquire("key_a", limits=overrides), RateLimited)


def test_render_quota_rejects_after_daily_limit(settings: Settings) -> None:
    wall = FakeWallClock(datetime(2026, 10, 14, 12, 0, tzinfo=UTC))
    limiter = TokenBucketLimiter(settings, now_fn=wall)

    # Quota is 2 per day: the first two renders pass, the third is limited.
    assert isinstance(limiter.try_render("key_a"), Allowed)
    assert isinstance(limiter.try_render("key_a"), Allowed)
    result = limiter.try_render("key_a")
    assert isinstance(result, RateLimited)
    # retry_after points at the next UTC midnight (12h away) — positive.
    assert result.retry_after_seconds == pytest.approx(12 * 3600)


def test_render_quota_resets_at_utc_day_rollover(settings: Settings) -> None:
    wall = FakeWallClock(datetime(2026, 10, 14, 23, 0, tzinfo=UTC))
    limiter = TokenBucketLimiter(settings, now_fn=wall)

    limiter.try_render("key_a")
    limiter.try_render("key_a")
    assert isinstance(limiter.try_render("key_a"), RateLimited)

    # Cross into the next UTC day: the counter resets.
    wall.advance(hours=2)
    assert isinstance(limiter.try_render("key_a"), Allowed)


def test_render_quota_isolated_per_key(settings: Settings) -> None:
    wall = FakeWallClock(datetime(2026, 10, 14, 12, 0, tzinfo=UTC))
    limiter = TokenBucketLimiter(settings, now_fn=wall)

    limiter.try_render("key_a")
    limiter.try_render("key_a")
    assert isinstance(limiter.try_render("key_a"), RateLimited)
    # Another key still has its full quota.
    assert isinstance(limiter.try_render("key_b"), Allowed)
