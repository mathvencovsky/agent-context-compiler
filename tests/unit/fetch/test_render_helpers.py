"""Browser-free checks of the render helpers (M10b): no Chromium needed."""

from __future__ import annotations

import threading
import time

import pytest

from app.config import Settings
from app.fetch import render
from app.fetch.errors import Timeout


def test_render_slots_bound_concurrent_renders() -> None:
    settings = Settings(render_concurrency=1, render_total_timeout_s=60)
    held = threading.Event()
    release = threading.Event()

    def holder() -> None:
        with render._render_slot(settings, time.monotonic() + 5, "http://a/"):
            held.set()
            release.wait(5)

    thread = threading.Thread(target=holder)
    thread.start()
    try:
        assert held.wait(5)
        started = time.monotonic()
        with pytest.raises(Timeout):
            with render._render_slot(settings, time.monotonic() + 0.2, "http://b/"):
                pass
        assert time.monotonic() - started < 2
    finally:
        release.set()
        thread.join()
    # Released: the slot is free again.
    with render._render_slot(settings, time.monotonic() + 0.2, "http://c/"):
        pass


# --------------------------------------------------------------------------- #
# Stable-text wait (no browser: a scripted fake page)
# --------------------------------------------------------------------------- #
class _FakePage:
    """Returns scripted text lengths; ``wait_for_timeout`` really sleeps."""

    def __init__(self, lengths: list[int]) -> None:
        self._lengths = lengths
        self.samples = 0

    def evaluate(self, _js: str) -> int:
        value = self._lengths[min(self.samples, len(self._lengths) - 1)]
        self.samples += 1
        return value

    def wait_for_timeout(self, ms: float) -> None:
        time.sleep(ms / 1000)

    def is_closed(self) -> bool:
        return False


_FAST = Settings(
    render_stable_interval_ms=5,
    render_stable_window_ms=10,
    render_stable_cap_ms=1000,
    render_min_text_chars=500,
)


def _wait(page: _FakePage, in_flight: int, settings: Settings = _FAST) -> str:
    tracker = render._InFlight()
    tracker.count = in_flight
    return render._wait_for_stable_text(
        page,  # type: ignore[arg-type]
        tracker,
        settings,
        time.monotonic() + 30,
    )


def test_long_stable_text_stops_even_with_requests_in_flight() -> None:
    page = _FakePage([100, 800, 800, 800, 800])
    assert _wait(page, in_flight=3) == "stable"
    assert page.samples == 4  # 800 seen three times = unchanged for 2 intervals


def test_short_text_waits_while_requests_are_in_flight() -> None:
    page = _FakePage([20])
    started = time.monotonic()
    assert _wait(page, in_flight=1) == "cap"
    assert time.monotonic() - started >= 0.9


def test_short_text_stops_when_nothing_is_in_flight() -> None:
    page = _FakePage([20, 20, 20])
    assert _wait(page, in_flight=0) == "stable"


def test_changing_text_is_not_stable() -> None:
    page = _FakePage(list(range(600, 100_000, 7)))
    assert _wait(page, in_flight=0) == "cap"


def test_cap_never_outlives_the_render_deadline() -> None:
    tracker = render._InFlight()
    tracker.count = 1
    started = time.monotonic()
    result = render._wait_for_stable_text(
        _FakePage([1]),  # type: ignore[arg-type]
        tracker,
        Settings(render_stable_interval_ms=5, render_stable_cap_ms=60_000),
        time.monotonic() + 0.1,
    )
    assert result == "cap"
    assert time.monotonic() - started < 1


# --------------------------------------------------------------------------- #
# Route handler: what is aborted before the guard
# --------------------------------------------------------------------------- #
class _FakeRequest:
    def __init__(self, url: str, resource_type: str, navigation: bool = False):
        self.url = url
        self.resource_type = resource_type
        self._navigation = navigation

    def is_navigation_request(self) -> bool:
        return self._navigation


class _FakeRoute:
    def __init__(self) -> None:
        self.outcome: str | None = None

    def abort(self) -> None:
        self.outcome = "abort"

    def continue_(self) -> None:
        self.outcome = "continue"


class _PublicResolver:
    def resolve(self, _host: str) -> list[str]:
        return ["93.184.216.34"]


@pytest.mark.parametrize(
    ("url", "resource_type", "navigation", "expected"),
    [
        ("http://docs.test/a.png", "image", False, "abort"),
        ("http://docs.test/beacon", "ping", False, "abort"),
        ("http://docs.test/stream", "eventsource", False, "abort"),
        ("https://www.googletagmanager.com/gtm.js", "script", False, "abort"),
        ("https://region1.google-analytics.com/g/collect", "fetch", False, "abort"),
        ("https://cdn.segment.com/analytics.js", "script", False, "abort"),
        # A navigation is never blocked by the analytics list (a page on such
        # a host still renders); it goes to the guard like any request.
        ("https://www.googletagmanager.com/", "document", True, "continue"),
        # Look-alike hosts are not on the list.
        ("https://notgoogletagmanager.com/x.js", "script", False, "continue"),
        ("https://segment.com/docs/", "document", True, "continue"),
        ("http://docs.test/app.js", "script", False, "continue"),
        ("http://docs.test/api", "fetch", False, "continue"),
    ],
)
def test_route_handler_blocks_no_content_requests(
    url: str, resource_type: str, navigation: bool, expected: str
) -> None:
    handler = render._make_route_handler(_PublicResolver(), Settings())
    route = _FakeRoute()
    handler(route, _FakeRequest(url, resource_type, navigation))  # type: ignore[arg-type]
    assert route.outcome == expected
