"""robots.txt fetching, caching and policy (SPEC §3.2, M1).

``RobotsChecker`` fetches ``/robots.txt`` for a domain through the SAME guarded
client as regular requests (so robots lookups are themselves SSRF-safe), caches
the parsed rules per domain for a configurable TTL (24h by default), and raises
a typed error when a path is disallowed.

Failure semantics (fail closed):
  * a clean 4xx (no robots.txt) => allowed for everything;
  * a 5xx, a timeout, or a blocked/invalid robots URL => ``RobotsUnavailable``
    (never cached as "allow");
  * a path matching ``Disallow`` for our own UA, or for ``*`` when no own-UA
    group exists, => ``RobotsDisallowed``.

Both the clock and the fetch callable are injectable so tests are deterministic
and never touch the network.

Process-wide cache (M10b efficiency, DECISIONS.md 2026-10-29): the API keeps ONE
checker per process (``app.api.documents.provide_robots_checker``), so the TTL
holds across requests instead of dying with each request's checker. The cache
is therefore thread-safe and bounded: at most ``max_entries`` origins (least
recently used evicted first), and concurrent lookups of the same origin wait
for a single robots.txt fetch instead of each fetching it.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol
from urllib import robotparser
from urllib.parse import urlsplit, urlunsplit

from app.config import Settings, get_settings
from app.fetch.client import FetchResult
from app.fetch.client import fetch as _default_fetch
from app.fetch.errors import FetchError, RobotsDisallowed, RobotsUnavailable


class _Fetcher(Protocol):
    def __call__(self, url: str) -> FetchResult: ...


#: Default bound on cached origins (one parsed robots.txt each, a few KB).
DEFAULT_MAX_ENTRIES = 1024
#: Striped locks serializing the fetch of one origin (bounded, never cleaned).
_FETCH_LOCK_STRIPES = 64


@dataclass
class _CacheEntry:
    parser: robotparser.RobotFileParser
    fetched_at: float


class RobotsChecker:
    """Per-domain robots.txt gate with a TTL cache.

    Parameters
    ----------
    fetch_fn:
        Callable returning a :class:`FetchResult` for a URL. Defaults to the
        guarded :func:`app.fetch.client.fetch`, so robots lookups go through the
        same SSRF guard. Tests inject a stub.
    time_fn:
        Monotonic clock source. Defaults to :func:`time.monotonic`; tests inject
        a controllable fake to exercise TTL expiry.
    settings:
        Runtime settings (TTL, UA). Defaults to the process settings.
    max_entries:
        Most origins kept in the cache; the least recently used is evicted.
    """

    def __init__(
        self,
        *,
        fetch_fn: _Fetcher | None = None,
        time_fn: Callable[[], float] = time.monotonic,
        settings: Settings | None = None,
        max_entries: int = DEFAULT_MAX_ENTRIES,
    ) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be at least 1")
        self._settings = settings or get_settings()
        self._fetch: _Fetcher = fetch_fn or _default_fetch
        self._time = time_fn
        self._max_entries = max_entries
        self._cache: OrderedDict[str, _CacheEntry] = OrderedDict()
        self._cache_lock = threading.Lock()
        self._fetch_locks = tuple(threading.Lock() for _ in range(_FETCH_LOCK_STRIPES))

    def is_allowed(self, url: str, *, user_agent: str | None = None) -> None:
        """Return ``None`` if ``url`` may be fetched, else raise a typed error.

        Raises ``RobotsDisallowed`` if robots.txt forbids the path, or
        ``RobotsUnavailable`` if robots.txt could not be retrieved.
        """
        ua = user_agent or self._settings.user_agent()
        origin, path = self._split_origin(url)
        parser = self._get_parser(origin)

        if not parser.can_fetch(ua, path):
            raise RobotsDisallowed(
                f"robots.txt disallows {path!r} for user-agent {ua!r}",
                details={"url": url, "user_agent": ua},
            )

    def _split_origin(self, url: str) -> tuple[str, str]:
        """Return ``(scheme://host[:port], path-with-query)`` for ``url``."""
        parts = urlsplit(url)
        origin = urlunsplit((parts.scheme, parts.netloc, "", "", ""))
        path = parts.path or "/"
        if parts.query:
            path = f"{path}?{parts.query}"
        return origin, path

    def _get_parser(self, origin: str) -> robotparser.RobotFileParser:
        """Return a fresh-enough parser for ``origin``, fetching if needed.

        A miss takes the origin's fetch lock and looks again, so threads that
        miss together wait for one fetch. A failed fetch raises and caches
        nothing (fail closed, as before).
        """
        cached = self._cached(origin)
        if cached is not None:
            return cached
        stripe = hash(origin) % len(self._fetch_locks)
        with self._fetch_locks[stripe]:
            cached = self._cached(origin)
            if cached is not None:
                return cached
            now = self._time()
            parser = self._fetch_rules(origin)
            with self._cache_lock:
                self._cache[origin] = _CacheEntry(parser=parser, fetched_at=now)
                self._cache.move_to_end(origin)
                while len(self._cache) > self._max_entries:
                    self._cache.popitem(last=False)
            return parser

    def _cached(self, origin: str) -> robotparser.RobotFileParser | None:
        """The cached parser for ``origin`` if still within the TTL, else None."""
        with self._cache_lock:
            entry = self._cache.get(origin)
            if entry is None or (self._time() - entry.fetched_at) >= self._ttl():
                return None
            self._cache.move_to_end(origin)
            return entry.parser

    def _ttl(self) -> int:
        return self._settings.robots_cache_ttl_seconds

    def _fetch_rules(self, origin: str) -> robotparser.RobotFileParser:
        """Fetch and parse ``origin/robots.txt`` applying SPEC §3.2 semantics."""
        robots_url = f"{origin}/robots.txt"
        parser = robotparser.RobotFileParser()

        try:
            result = self._fetch(robots_url)
        except FetchError as exc:
            # Blocked/invalid/timeout/transport: robots cannot be trusted.
            raise RobotsUnavailable(
                f"could not fetch {robots_url}: {exc.code}",
                details={"robots_url": robots_url, "cause": exc.code},
            ) from exc

        status = result.status
        if 400 <= status < 500:
            # No robots.txt => allow everything. An empty rule set makes
            # RobotFileParser.can_fetch return True for every path/UA.
            parser.parse([])
            return parser
        if status >= 500:
            raise RobotsUnavailable(
                f"robots.txt at {robots_url} returned {status}",
                details={"robots_url": robots_url, "status": status},
            )

        text = result.body.decode("utf-8", errors="replace")
        parser.parse(text.splitlines())
        return parser
