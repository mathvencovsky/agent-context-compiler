"""Playwright rendering with guard-enforced request interception (SPEC §3.4).

This is the browser network path of M4. Unlike the M1 httpx client
(``app/fetch/client.py``), which connects on the guard-validated IP and so
closes the DNS-rebinding window, Chromium performs its OWN DNS resolution and
exposes no connect-on-IP seam. The guarantee here is therefore *per-request
validation* across BOTH of Chromium's outbound interception seams:

* HTTP(S) requests — ``page.route("**/*")`` intercepts every HTTP request the
  page makes (the top-level navigation AND every sub-request: scripts,
  XHR/fetch, images, fonts, media, ...), routes its URL through
  ``app.fetch.guard.validate_url`` and ``route.abort()``-s any destination the
  guard rejects.
* WebSocket connections — ``page.route("**/*")`` does NOT see WebSockets in this
  Playwright build (they travel over a separate seam), so a second handler is
  registered with ``page.route_web_socket("**/*")``. It rewrites the WS scheme
  for validation (``ws`` → ``http``, ``wss`` → ``https``) so the guard's
  http/https IP + port + scheme policy applies unchanged, and connects to the
  real server (``connect_to_server()``) ONLY when the guard accepts. On
  rejection it does not connect — Chromium never reaches the destination.

Any destination the guard rejects (private/loopback/link-local IPs, the
``169.254.169.254`` metadata address, disallowed ports/schemes,
alternate-notation IP literals, ...) is refused on both seams. The guard is the
SINGLE destination authority: no IP or port policy is duplicated here.

``httpx`` is deliberately NOT imported in this module — the browser is the only
outbound path and it is funnelled through the guard, keeping the CLAUDE.md rule
that direct network clients live only behind the fetch guard.

Browser provenance contract
----------------------------
``render_url`` returns a frozen :class:`RenderResult` (``final_url``, ``html``,
``status``). The caller (the M5 resolve path) uses it to build the document:
set ``SourceInfo.render_mode = "browser"`` and append a
``Warning(code="rendered_with_browser", message=..., block_id=None)``. The
``RENDERED_WITH_BROWSER_WARNING_CODE`` constant below is exported so the caller
and the tests agree on the exact warning code string.

See DECISIONS.md (2026-10-11, "M4 — janela de DNS rebinding no navegador") for
the honest statement of the residual rebinding window and the M10 egress
mitigation.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from playwright.sync_api import Request, Route, WebSocketRoute, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from app.config import Settings, get_settings
from app.fetch.errors import FetchError
from app.fetch.guard import Resolver, validate_url

#: Warning code the caller appends when a document was rendered with the browser.
RENDERED_WITH_BROWSER_WARNING_CODE = "rendered_with_browser"

#: Resource types aborted purely for bandwidth economy (SPEC §3.4). These never
#: reach the guard check — they are dropped first — but even if they did they
#: would be validated like any other request.
_ECONOMY_RESOURCE_TYPES = frozenset({"image", "media", "font"})


@dataclass(frozen=True)
class RenderResult:
    """Typed result of a successful browser render (SPEC §3.4).

    ``final_url`` is the page URL after any in-browser navigation/redirects;
    ``html`` is ``page.content()`` (the serialized post-render DOM); ``status``
    is the HTTP status of the main navigation response, or ``None`` when
    Playwright reported no response (e.g. a document served from cache).
    """

    final_url: str
    html: str
    status: int | None


def _make_route_handler(
    resolver: Resolver | None,
    settings: Settings,
) -> Callable[[Route, Request], None]:
    """Build the ``page.route`` handler closing over the guard dependencies.

    The handler is fail-closed and total: image/media/font requests are aborted
    for economy; every other request's URL is validated by the guard and
    aborted on ANY :class:`FetchError`; and any unexpected error also aborts, so
    no request ever proceeds to an unvalidated destination.
    """

    def handle(route: Route, request: Request) -> None:
        if request.resource_type in _ECONOMY_RESOURCE_TYPES:
            route.abort()
            return
        try:
            validate_url(request.url, resolver=resolver, settings=settings)
        except FetchError:
            route.abort()
            return
        except Exception:  # noqa: BLE001 — fail closed on any unexpected error.
            route.abort()
            return
        route.continue_()

    return handle


def _ws_probe_url(ws_url: str) -> str:
    """Rewrite a ``ws(s)://`` URL to ``http(s)://`` for guard validation.

    The guard's policy is defined over http/https; a WebSocket destination is the
    same host/port/IP reachability question, so mapping the scheme lets the guard
    vet it unchanged (``ws`` → ``http`` default port 80, ``wss`` → ``https``
    default port 443). Any non-``ws`` scheme is returned untouched so the guard
    rejects it as an unsupported scheme (fail-closed).
    """
    if ws_url.startswith("wss://"):
        return "https://" + ws_url[len("wss://") :]
    if ws_url.startswith("ws://"):
        return "http://" + ws_url[len("ws://") :]
    return ws_url


def _make_ws_route_handler(
    resolver: Resolver | None,
    settings: Settings,
) -> Callable[[WebSocketRoute], None]:
    """Build the ``page.route_web_socket`` handler closing over the guard deps.

    WebSocket connections are not covered by ``page.route`` in this Playwright
    build, so they are intercepted here. The destination URL is validated by the
    guard (after rewriting ``ws(s)`` → ``http(s)``); the connection to the real
    server is established ONLY when the guard accepts. On ANY error — a guard
    rejection or an unexpected exception — the handler returns WITHOUT calling
    ``connect_to_server()``, so Chromium's socket is never wired to the
    destination (fail-closed, mirroring the HTTP ``route.abort()`` path).
    """

    def handle_ws(ws: WebSocketRoute) -> None:
        try:
            validate_url(_ws_probe_url(ws.url), resolver=resolver, settings=settings)
        except FetchError:
            return
        except Exception:  # noqa: BLE001 — fail closed on any unexpected error.
            return
        ws.connect_to_server()

    return handle_ws


def render_url(
    url: str,
    *,
    resolver: Resolver | None = None,
    settings: Settings | None = None,
    browser_launch_args: list[str] | None = None,
) -> RenderResult:
    """Render ``url`` in a fresh ephemeral Chromium context and return its HTML.

    The entry URL is validated by the guard BEFORE launching the browser (so an
    illegal entry point fails fast with the guard's typed error), and then every
    request the page makes — navigation and sub-requests alike — is re-validated
    by the ``page.route("**/*")`` handler (see module docstring). Image, media
    and font requests are aborted for economy.

    After navigation the page waits for ``domcontentloaded`` and then
    ``networkidle``, each capped at ``settings.render_networkidle_timeout_ms``
    (SPEC §3.4, default 15 s). A networkidle timeout is NOT fatal: whatever
    loaded is captured anyway via ``page.content()``.

    The browser and context are always torn down (try/finally). ``resolver`` is
    forwarded to the guard so tests can inject a deterministic resolver; in
    production the guard's default :class:`SocketResolver` is used.

    ``browser_launch_args`` is an operational escape hatch forwarded verbatim to
    Chromium's command line (``launch(args=...)``) — e.g. sandbox flags under
    hardened CI, or ``--host-resolver-rules`` in the browser tests to point a
    name at a loopback fixture WITHOUT changing what the guard validates. It is
    NOT a destination-policy lever: the guard still vets every intercepted URL.
    """
    settings = settings or get_settings()

    # Guard the entry point itself before paying for a browser launch. This
    # raises the guard's typed error (InvalidURL/BlockedDestination/...) for an
    # illegal entry URL, exactly like the M1 client.
    validate_url(url, resolver=resolver, settings=settings)

    timeout_ms = settings.render_networkidle_timeout_ms
    handler = _make_route_handler(resolver, settings)
    ws_handler = _make_ws_route_handler(resolver, settings)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(args=browser_launch_args or [])
        try:
            # Fresh ephemeral context per call: no persistence, no downloads,
            # no extra permissions.
            context = browser.new_context(accept_downloads=False)
            try:
                page = context.new_page()
                # Register interception BEFORE navigating so the top-level
                # navigation is itself routed through the guard. WebSocket
                # connections travel over a separate seam that page.route does
                # NOT cover, so register page.route_web_socket as well — also
                # before navigation, since only WebSockets created afterwards
                # are routed.
                page.route_web_socket("**/*", ws_handler)
                page.route("**/*", handler)

                response = page.goto(url, wait_until="domcontentloaded")
                try:
                    page.wait_for_load_state("networkidle", timeout=timeout_ms)
                except PlaywrightTimeoutError:
                    # networkidle never settled within the cap: proceed with
                    # whatever loaded rather than hanging (SPEC §3.4).
                    pass

                html = page.content()
                final_url = page.url
                status = response.status if response is not None else None
                return RenderResult(final_url=final_url, html=html, status=status)
            finally:
                context.close()
        finally:
            browser.close()
