"""Usage accounting + per-key rate limit for the /v1 surface — M9 (SPEC §8).

Three pieces wire together here:

* :class:`UsageMiddleware` — a pure ASGI middleware restricted to paths under
  ``/v1`` (``/health`` and everything else pass straight through). Like
  :class:`~app.logging.RequestIdMiddleware` it times the request and captures
  the response status via a ``send`` wrapper; AFTER the response completes it
  writes ONE :class:`~app.store.models.UsageEvent` for the authenticated key
  (read from the ASGI ``scope["state"]`` the auth dependency populated), opening
  its own session from an injectable factory. It only RECORDS — it never
  rejects.
* :func:`require_quota` — a FastAPI dependency attached to the /v1 routers AFTER
  :func:`~app.api.auth.require_api_key`. It spends one token from the per-key
  token bucket and raises :class:`~app.api.errors.RateLimited` -> 429 BEFORE the
  handler runs, so an over-limit request never does work. In dev (no
  authenticated key) it is a no-op, keeping the M0-M8 tests green.
* :func:`charge_render` — called by the resolve/assemble handlers the moment a
  browser render actually happens; it charges the per-key DAILY RENDER QUOTA and
  raises :class:`RateLimited` when the quota is spent. The daily quota reuses the
  ``rate_limited`` code (documented choice). The handler also records how many
  ``output_tokens``/``renders`` the request consumed on ``request.state`` for
  the middleware to persist.
* ``GET /v1/usage`` — returns the current key's usage aggregated by day and by
  endpoint (:class:`~app.api.schemas.UsageResponse`).

The limiter, the session factory and the settings are injectable ``Depends``
seams; the limiter in particular MUST be injectable so a test can hand each case
a fresh :class:`~app.usage.limits.TokenBucketLimiter` with a controllable clock
and no cross-test state leak.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session, sessionmaker
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.auth import REQUEST_STATE_API_KEY, require_api_key
from app.api.errors import RateLimited
from app.api.schemas import UsageByDay, UsageByEndpoint, UsageResponse
from app.config import Settings, get_settings
from app.logging import CLIENT_HEADER, logger
from app.store.models import ApiKey
from app.store.repo import make_session_factory, shared_engine
from app.usage.events import UsageBucket, UsageEventRepo
from app.usage.limits import RateLimited as LimitRateLimited
from app.usage.limits import TokenBucketLimiter

#: /v1 scope prefix the usage middleware and quota act on.
_V1_PREFIX = "/v1"

#: scope["state"] key the handlers set to tell the middleware how many
#: output_tokens / renders a request consumed (middleware records them).
REQUEST_STATE_USAGE_UNITS = "usage_units"

#: scope["state"] key carrying the limiter the quota dependency used, so a
#: handler can charge the render quota against the SAME limiter instance.
REQUEST_STATE_LIMITER = "usage_limiter"
#: scope["state"] key telling the middleware to record a keyless (local) request.
REQUEST_STATE_RECORD_LOCAL = "usage_record_local"
#: What an MCP client may call itself in the record (it is not authenticated).
_CLIENT_NAME = re.compile(r"[A-Za-z0-9._-]{1,40}")


def records_local_usage(settings: Settings) -> bool:
    """Whether keyless requests are recorded (ENV=dev + ``record_local_usage``)."""
    return settings.env == "dev" and settings.record_local_usage


# --------------------------------------------------------------------------- #
# Process-wide limiter (single instance; injectable/overridable in tests)
# --------------------------------------------------------------------------- #
_LIMITER: TokenBucketLimiter | None = None


def provide_limiter() -> TokenBucketLimiter:
    """Return the process-wide rate limiter (overridable in tests).

    Lazily constructed from the process settings. Tests override this seam with
    a fresh :class:`TokenBucketLimiter` (controllable clock) so no bucket state
    leaks between cases.
    """
    global _LIMITER
    if _LIMITER is None:
        _LIMITER = TokenBucketLimiter(get_settings())
    return _LIMITER


def provide_usage_settings() -> Settings:
    """Return the process settings for the usage layer (overridable in tests)."""
    return get_settings()


def provide_usage_session_factory() -> sessionmaker[Session]:
    """Build a session factory bound to the configured database (overridable)."""
    return make_session_factory(shared_engine(get_settings()))


LimiterDep = Annotated[TokenBucketLimiter, Depends(provide_limiter)]
UsageSettingsDep = Annotated[Settings, Depends(provide_usage_settings)]
UsageSessionFactoryDep = Annotated[
    sessionmaker[Session], Depends(provide_usage_session_factory)
]


# --------------------------------------------------------------------------- #
# Quota dependency (runs AFTER require_api_key, BEFORE the handler)
# --------------------------------------------------------------------------- #
def require_quota(
    request: Request,
    limiter: LimiterDep,
    settings: UsageSettingsDep,
) -> None:
    """Spend one token from the per-key bucket before the handler runs.

    No-op when there is no authenticated key (dev / no-op auth). Raises
    :class:`RateLimited` -> 429 with ``Retry-After`` when the bucket is empty.
    Stashes the limiter on ``request.state`` so a handler can charge the daily
    render quota against the same instance via :func:`charge_render`.
    """
    setattr(request.state, REQUEST_STATE_LIMITER, limiter)
    api_key = getattr(request.state, REQUEST_STATE_API_KEY, None)
    if api_key is None:
        setattr(
            request.state, REQUEST_STATE_RECORD_LOCAL, records_local_usage(settings)
        )
        return

    result = limiter.acquire(api_key.id, cost=1.0, limits=dict(api_key.limits or {}))
    if isinstance(result, LimitRateLimited):
        raise RateLimited(
            "rate limit exceeded",
            retry_after=result.retry_after_seconds,
        )


QuotaDep = Annotated[None, Depends(require_quota)]


# --------------------------------------------------------------------------- #
# Render-quota + units accounting (called by the resolve/assemble handlers)
# --------------------------------------------------------------------------- #
def charge_render(request: Request) -> None:
    """Charge one browser render against the per-key daily render quota.

    Called by a handler the moment it decides to render with a headless browser.
    No-op without an authenticated key or a limiter on ``request.state`` (dev).
    Raises :class:`RateLimited` -> 429 (``Retry-After`` until the next UTC
    midnight) when the key's daily render quota is exhausted.
    """
    api_key = getattr(request.state, REQUEST_STATE_API_KEY, None)
    limiter = getattr(request.state, REQUEST_STATE_LIMITER, None)
    if api_key is None or not isinstance(limiter, TokenBucketLimiter):
        return
    result = limiter.try_render(api_key.id, limits=dict(api_key.limits or {}))
    if isinstance(result, LimitRateLimited):
        raise RateLimited(
            "daily render quota exceeded",
            retry_after=result.retry_after_seconds,
            details={"quota": "render_daily"},
        )


def set_usage_units(request: Request, units: dict[str, object]) -> None:
    """Record the request's usage counters for the middleware to persist.

    The handlers call this with ``{output_tokens, renders}`` (assemble) or
    ``{renders}`` (resolve); the :class:`UsageMiddleware` reads it back off the
    ASGI ``scope["state"]`` after the response and stores it on the event.
    Later calls add to the earlier counters of the same request.
    """
    known = getattr(request.state, REQUEST_STATE_USAGE_UNITS, None) or {}
    setattr(request.state, REQUEST_STATE_USAGE_UNITS, {**known, **units})


def source_hosts(urls: Iterable[str]) -> list[str]:
    """The host names of ``urls``: never a path, a query or credentials."""
    hosts = set()
    for url in urls:
        try:
            hosts.add(urlsplit(url).hostname)
        except ValueError:
            continue
    return sorted(host for host in hosts if host)


# --------------------------------------------------------------------------- #
# GET /v1/usage
# --------------------------------------------------------------------------- #
# require_api_key gates /v1/usage (no-op in dev); require_quota charges it too.
router = APIRouter(dependencies=[Depends(require_api_key), Depends(require_quota)])


def _rollup_by_day(buckets: list[UsageBucket]) -> list[UsageByDay]:
    """Fold the per-(day, endpoint) buckets into the per-day response shape."""
    days: dict[str, list[UsageBucket]] = {}
    for bucket in buckets:
        days.setdefault(bucket.day, []).append(bucket)

    out: list[UsageByDay] = []
    for day in sorted(days):
        endpoint_buckets = sorted(days[day], key=lambda b: b.endpoint)
        endpoints = [
            UsageByEndpoint(
                endpoint=b.endpoint,
                requests=b.requests,
                tokens_delivered=b.tokens_delivered,
                page_tokens=b.page_tokens,
                renders=b.renders,
                latency_p50_ms=b.latency_p50_ms,
                latency_p95_ms=b.latency_p95_ms,
            )
            for b in endpoint_buckets
        ]
        out.append(
            UsageByDay(
                day=day,
                requests=sum(b.requests for b in endpoint_buckets),
                tokens_delivered=sum(b.tokens_delivered for b in endpoint_buckets),
                page_tokens=sum(b.page_tokens for b in endpoint_buckets),
                renders=sum(b.renders for b in endpoint_buckets),
                latency_p50_ms=max(b.latency_p50_ms for b in endpoint_buckets),
                latency_p95_ms=max(b.latency_p95_ms for b in endpoint_buckets),
                endpoints=endpoints,
            )
        )
    return out


@router.get("/v1/usage", response_model=UsageResponse)
def get_usage(
    request: Request,
    session_factory: UsageSessionFactoryDep,
    settings: UsageSettingsDep,
) -> UsageResponse:
    """Return the key's usage (dev: the keyless local use) by day and endpoint."""
    api_key = getattr(request.state, REQUEST_STATE_API_KEY, None)

    # Dev / no-op auth: no key means no attributed usage, unless local use is recorded.
    if api_key is None and not records_local_usage(settings):
        return UsageResponse(
            requests=0,
            tokens_delivered=0,
            page_tokens=0,
            renders=0,
            latency_p50_ms=0,
            latency_p95_ms=0,
            by_day=[],
        )

    session = session_factory()
    try:
        buckets = UsageEventRepo(session).aggregate(
            None if api_key is None else api_key.id
        )
    finally:
        session.close()

    by_day = _rollup_by_day(buckets)
    return UsageResponse(
        requests=sum(d.requests for d in by_day),
        tokens_delivered=sum(d.tokens_delivered for d in by_day),
        page_tokens=sum(d.page_tokens for d in by_day),
        renders=sum(d.renders for d in by_day),
        latency_p50_ms=max((d.latency_p50_ms for d in by_day), default=0),
        latency_p95_ms=max((d.latency_p95_ms for d in by_day), default=0),
        by_day=by_day,
    )


# --------------------------------------------------------------------------- #
# ASGI middleware: records ONE usage event per /v1 request (never rejects)
# --------------------------------------------------------------------------- #
class UsageMiddleware:
    """ASGI middleware that writes one usage event per authenticated /v1 request.

    Non-``http`` scopes and paths outside ``/v1`` pass through untouched. The
    request is timed and the response status captured via a ``send`` wrapper;
    AFTER the response completes, the authenticated key (set on the ASGI
    ``scope["state"]`` by the auth dependency) is read and — if present — one
    :class:`UsageEvent` is persisted via a freshly opened session. A write
    failure is logged and swallowed so usage accounting never breaks a served
    response (the failure is surfaced via the log, not a 500 to the caller).

    The ``units`` recorded come from ``scope["state"][usage_units]`` which the
    handlers set (assemble -> ``{output_tokens, renders}``; resolve ->
    ``{renders: 0|1}``); absent, an empty ``units`` is stored.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        session_factory: Callable[[], sessionmaker[Session]] | None = None,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self.app = app
        self._session_factory = session_factory
        self._now_fn = now_fn or (lambda: datetime.now(UTC))

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope.get("path", "").startswith(_V1_PREFIX):
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        start = time.perf_counter()
        status_code = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            latency_ms = int(round((time.perf_counter() - start) * 1000))
            self._record(
                scope, path=path, status_code=status_code, latency_ms=latency_ms
            )

    def _record(
        self, scope: Scope, *, path: str, status_code: int, latency_ms: int
    ) -> None:
        """Persist one usage event for the authenticated key, or keyless local use."""
        state = scope.get("state") or {}
        api_key = state.get(REQUEST_STATE_API_KEY)
        if (
            not isinstance(api_key, ApiKey)
            and state.get(REQUEST_STATE_RECORD_LOCAL) is not True
        ):
            return

        units = state.get(REQUEST_STATE_USAGE_UNITS)
        if not isinstance(units, dict):
            units = {}
        raw = dict(scope.get("headers") or []).get(CLIENT_HEADER.lower().encode())
        client = raw.decode("latin-1") if raw else ""
        if _CLIENT_NAME.fullmatch(client):
            units = {**units, "client": client}

        session = self._resolve_factory()()
        try:
            UsageEventRepo(session).record(
                api_key_id=api_key.id if isinstance(api_key, ApiKey) else None,
                endpoint=path,
                ts=self._now_fn(),
                status_code=status_code,
                latency_ms=latency_ms,
                units=units,
            )
            session.commit()
        except Exception:
            session.rollback()
            logger.warning("usage.record.error", path=path, exc_info=True)
        finally:
            session.close()

    def _resolve_factory(self) -> Callable[[], Session]:
        if self._session_factory is not None:
            return self._session_factory()
        return make_session_factory(shared_engine(get_settings()))


__all__ = [
    "REQUEST_STATE_USAGE_UNITS",
    "UsageMiddleware",
    "charge_render",
    "get_usage",
    "provide_limiter",
    "provide_usage_session_factory",
    "provide_usage_settings",
    "require_quota",
    "router",
    "set_usage_units",
    "source_hosts",
]
