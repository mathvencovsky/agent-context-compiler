"""robots.txt policy and caching (SPEC §3.2)."""

from __future__ import annotations

import httpx
import pytest

from app.config import Settings
from app.fetch.client import FetchResult
from app.fetch.errors import FetchFailed, RobotsDisallowed, RobotsUnavailable
from app.fetch.robots import RobotsChecker

SETTINGS = Settings()
UA = SETTINGS.user_agent()


class FetchStub:
    """Scripted, call-counting replacement for app.fetch.client.fetch."""

    def __init__(
        self, *, status: int = 200, body: bytes = b"", raises: Exception | None = None
    ):
        self.status = status
        self.body = body
        self.raises = raises
        self.calls: list[str] = []

    def __call__(self, url: str) -> FetchResult:
        self.calls.append(url)
        if self.raises is not None:
            raise self.raises
        return FetchResult(
            final_url=url,
            status=self.status,
            headers=httpx.Headers({"content-type": "text/plain"}),
            body=self.body,
            content_type="text/plain",
            redirects=0,
            elapsed_ms=1,
        )


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


_ROBOTS_DISALLOW_ALL = b"User-agent: *\nDisallow: /\n"
_ROBOTS_OWN_UA = b"User-agent: AgentContextBot\nDisallow: /private\n"
_ROBOTS_ALLOW = b"User-agent: *\nDisallow: /nope\n"


def _checker(stub: FetchStub, clock: FakeClock | None = None) -> RobotsChecker:
    return RobotsChecker(
        fetch_fn=stub,
        time_fn=clock or FakeClock(),
        settings=SETTINGS,
    )


def test_disallow_for_star_blocks() -> None:
    stub = FetchStub(status=200, body=_ROBOTS_DISALLOW_ALL)
    checker = _checker(stub)
    with pytest.raises(RobotsDisallowed):
        checker.is_allowed("http://example.com/anything", user_agent=UA)
    assert stub.calls == ["http://example.com/robots.txt"]


def test_disallow_for_own_ua_blocks() -> None:
    stub = FetchStub(status=200, body=_ROBOTS_OWN_UA)
    checker = _checker(stub)
    with pytest.raises(RobotsDisallowed):
        checker.is_allowed("http://example.com/private/secret", user_agent=UA)


def test_allowed_path_returns_none() -> None:
    stub = FetchStub(status=200, body=_ROBOTS_ALLOW)
    checker = _checker(stub)
    assert checker.is_allowed("http://example.com/docs", user_agent=UA) is None


def test_4xx_means_allowed() -> None:
    stub = FetchStub(status=404, body=b"")
    checker = _checker(stub)
    assert checker.is_allowed("http://example.com/anything", user_agent=UA) is None


def test_5xx_means_unavailable() -> None:
    stub = FetchStub(status=503, body=b"")
    checker = _checker(stub)
    with pytest.raises(RobotsUnavailable):
        checker.is_allowed("http://example.com/x", user_agent=UA)


def test_fetch_error_means_unavailable() -> None:
    stub = FetchStub(raises=FetchFailed("boom"))
    checker = _checker(stub)
    with pytest.raises(RobotsUnavailable):
        checker.is_allowed("http://example.com/x", user_agent=UA)


def test_cache_hit_does_not_refetch() -> None:
    stub = FetchStub(status=200, body=_ROBOTS_ALLOW)
    clock = FakeClock()
    checker = _checker(stub, clock)

    checker.is_allowed("http://example.com/a", user_agent=UA)
    clock.now = 100.0  # still well within the 24h TTL
    checker.is_allowed("http://example.com/b", user_agent=UA)

    assert stub.calls == ["http://example.com/robots.txt"]


def test_cache_expiry_refetches() -> None:
    stub = FetchStub(status=200, body=_ROBOTS_ALLOW)
    clock = FakeClock()
    checker = _checker(stub, clock)

    checker.is_allowed("http://example.com/a", user_agent=UA)
    clock.now = SETTINGS.robots_cache_ttl_seconds + 1  # past TTL
    checker.is_allowed("http://example.com/b", user_agent=UA)

    assert stub.calls == [
        "http://example.com/robots.txt",
        "http://example.com/robots.txt",
    ]


def test_distinct_origins_cached_separately() -> None:
    stub = FetchStub(status=200, body=_ROBOTS_ALLOW)
    checker = _checker(stub)
    checker.is_allowed("http://a.example.com/x", user_agent=UA)
    checker.is_allowed("http://b.example.com/x", user_agent=UA)
    assert stub.calls == [
        "http://a.example.com/robots.txt",
        "http://b.example.com/robots.txt",
    ]
