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


# --------------------------------------------------------------------------- #
# Process-wide cache (M10b efficiency): shared across requests, bounded,
# thread-safe, one fetch per origin under concurrency.
# --------------------------------------------------------------------------- #
def test_api_checker_is_one_per_process() -> None:
    """The API seam returns the same checker every time, so its TTL cache
    survives across requests (it used to be rebuilt per request)."""
    from app.api.documents import provide_robots_checker

    assert provide_robots_checker() is provide_robots_checker()


def test_cache_survives_across_requests_through_api_seam(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two 'requests' (two seam calls) on one origin fetch robots.txt once."""
    from app.api import documents

    stub = FetchStub(body=_ROBOTS_ALLOW)
    documents.provide_robots_checker.cache_clear()
    monkeypatch.setattr(
        documents,
        "RobotsChecker",
        lambda: RobotsChecker(fetch_fn=stub, time_fn=FakeClock(), settings=SETTINGS),
    )
    try:
        documents.provide_robots_checker().is_allowed("https://docs.example.com/a")
        documents.provide_robots_checker().is_allowed("https://docs.example.com/b")
    finally:
        documents.provide_robots_checker.cache_clear()
    assert stub.calls == ["https://docs.example.com/robots.txt"]


def test_cache_is_bounded_lru() -> None:
    """Past ``max_entries`` origins the least recently used one is evicted."""
    stub = FetchStub(body=_ROBOTS_ALLOW)
    checker = RobotsChecker(
        fetch_fn=stub, time_fn=FakeClock(), settings=SETTINGS, max_entries=2
    )
    checker.is_allowed("https://a.example/x")
    checker.is_allowed("https://b.example/x")
    checker.is_allowed("https://a.example/y")  # a is now most recent
    checker.is_allowed("https://c.example/x")  # evicts b
    assert len(stub.calls) == 3
    checker.is_allowed("https://a.example/z")  # still cached
    assert len(stub.calls) == 3
    checker.is_allowed("https://b.example/z")  # was evicted: refetched
    assert stub.calls[-1] == "https://b.example/robots.txt"
    assert len(stub.calls) == 4


def test_max_entries_must_be_positive() -> None:
    with pytest.raises(ValueError):
        RobotsChecker(fetch_fn=FetchStub(), settings=SETTINGS, max_entries=0)


def test_failure_is_not_cached_and_is_retried() -> None:
    """Fail closed stays: an unavailable robots.txt is never cached as allow."""
    stub = FetchStub(status=503)
    checker = _checker(stub)
    for _ in range(2):
        with pytest.raises(RobotsUnavailable):
            checker.is_allowed("https://docs.example.com/a")
    assert len(stub.calls) == 2


def test_concurrent_misses_fetch_once() -> None:
    """Threads that miss the same origin together share one robots fetch."""
    import threading
    import time

    calls: list[str] = []
    calls_lock = threading.Lock()

    def slow_fetch(url: str) -> FetchResult:
        with calls_lock:
            calls.append(url)
        time.sleep(0.05)
        return FetchStub(body=_ROBOTS_DISALLOW_ALL)(url)

    checker = RobotsChecker(fetch_fn=slow_fetch, time_fn=FakeClock(), settings=SETTINGS)
    outcomes: list[str] = []
    out_lock = threading.Lock()

    def worker(i: int) -> None:
        try:
            checker.is_allowed(f"https://docs.example.com/p{i}")
            result = "allowed"
        except RobotsDisallowed:
            result = "disallowed"
        with out_lock:
            outcomes.append(result)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert calls == ["https://docs.example.com/robots.txt"]
    assert outcomes == ["disallowed"] * 8
