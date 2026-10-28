"""Playwright rendering behind a guard-enforcing egress proxy (SPEC §3.4).

This is the browser network path of M4. Chromium is not trusted to keep its
own traffic inside the guard: interception hooks (``page.route``) miss redirect
hops, popups, service workers and connection-level features such as
preconnect, so the audit of 2026-10-28 (docs/AUDITORIA_SEGURANCA.md, C1/A4/M3)
showed an SSRF with read-back. The guarantee is now enforced at the CONNECTION
level, where no browser feature can route around it:

* **Egress proxy (enforcement).** Chromium is launched with
  :class:`app.fetch.egress_proxy.EgressProxy` as ``--proxy-server`` (loopback
  NOT bypassed) and with ``--host-resolver-rules="MAP * ~NOTFOUND"`` so it
  cannot resolve any hostname by itself; non-proxied WebRTC UDP is disabled.
  Every HTTP/HTTPS/WebSocket connection — any redirect hop, sub-resource,
  popup, iframe, worker, prefetch — is a request to the proxy, which validates
  the destination with ``app.fetch.guard.validate_url`` and connects ONLY to
  the guard-validated IP. That also closes the browser DNS-rebinding window
  (Chromium never resolves; the proxy connects on the validated IP).
* **Context-level interception (economy and early refusal).**
  ``context.route("**/*")`` — on the BrowserContext, so popups are covered —
  aborts image/media/font requests for bandwidth economy (SPEC §3.4) and
  aborts any request whose URL the guard rejects before it reaches the proxy.
  ``context.route_web_socket`` does the same for WebSockets.
* **No service workers, no popups.** ``service_workers="block"`` and every page
  other than the one being rendered is closed as soon as it opens.
* **Resource limits (audit M2).** A wall-clock deadline
  (``settings.render_total_timeout_s``) covers the whole render; when it
  expires a watchdog kills the Chromium process (a JS busy loop otherwise
  hangs ``page.content()`` forever) and :class:`Timeout` is raised. The
  serialized DOM is capped at ``settings.max_body_bytes`` (:class:`TooLarge`).

``httpx`` is deliberately NOT imported in this module.

Browser provenance contract
----------------------------
``render_url`` returns a frozen :class:`RenderResult` (``final_url``, ``html``,
``status``). The caller (the M5 resolve path) uses it to build the document:
set ``SourceInfo.render_mode = "browser"`` and append a
``Warning(code="rendered_with_browser", message=..., block_id=None)``. The
``RENDERED_WITH_BROWSER_WARNING_CODE`` constant below is exported so the caller
and the tests agree on the exact warning code string.
"""

from __future__ import annotations

import os
import signal
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from playwright.sync_api import (
    Browser,
    Page,
    Request,
    Route,
    WebSocketRoute,
    sync_playwright,
)
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from app.config import Settings, get_settings
from app.fetch.egress_proxy import Connector, EgressProxy
from app.fetch.errors import FetchError, Timeout, TooLarge
from app.fetch.guard import Resolver, validate_url

#: Warning code the caller appends when a document was rendered with the browser.
RENDERED_WITH_BROWSER_WARNING_CODE = "rendered_with_browser"

#: Resource types aborted purely for bandwidth economy (SPEC §3.4). These never
#: reach the guard check — they are dropped first — but even if they did they
#: would be validated like any other request.
_ECONOMY_RESOURCE_TYPES = frozenset({"image", "media", "font"})

#: Navigation timeout of ``page.goto`` (Playwright's default, kept explicit).
_GOTO_TIMEOUT_MS = 30_000

_HOST_RULES_FLAG = "--host-resolver-rules="

#: Chromium flags that keep every connection on the proxy. Chromium cannot
#: resolve any name itself (the proxy address is a literal IP and excluded), and
#: WebRTC may not open UDP sockets that bypass the proxy.
_EGRESS_HOST_RULES = ("MAP * ~NOTFOUND", "EXCLUDE 127.0.0.1")
_EGRESS_FLAGS = (
    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
    "--disable-quic",
)


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
    """Build the ``context.route`` handler closing over the guard dependencies.

    The handler is fail-closed and total: image/media/font requests are aborted
    for economy; every other request's URL is validated by the guard and
    aborted on ANY error. A request that passes still goes through the egress
    proxy, which validates the connection again (the enforcement point).
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
    """Build the ``context.route_web_socket`` handler closing over the guard deps.

    The destination URL is validated by the guard (after rewriting ``ws(s)`` →
    ``http(s)``); the connection to the real server is established ONLY when the
    guard accepts (and then through the egress proxy). On ANY error the handler
    returns WITHOUT calling ``connect_to_server()`` (fail-closed).
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


def _launch_args(extra: list[str] | None) -> list[str]:
    """Chromium args: caller extras first, the egress flags last (they win).

    A caller ``--host-resolver-rules`` (the browser tests map fixture names to
    loopback with it) is MERGED in front of ``MAP * ~NOTFOUND``: it can only
    affect a connection that bypassed the proxy, which is exactly what the
    tests want to detect, and it can never re-enable resolution of other names.
    """
    args: list[str] = []
    rules: list[str] = []
    for arg in extra or []:
        if arg.startswith(_HOST_RULES_FLAG):
            rules.extend(r for r in arg[len(_HOST_RULES_FLAG) :].split(",") if r)
        else:
            args.append(arg)
    rules.extend(_EGRESS_HOST_RULES)
    return [*args, *_EGRESS_FLAGS, _HOST_RULES_FLAG + ",".join(rules)]


def _browser_pids(browser: Browser) -> list[int]:
    """PIDs of the Chromium processes, for the deadline watchdog."""
    session = browser.new_browser_cdp_session()
    try:
        info = session.send("SystemInfo.getProcessInfo")
    finally:
        session.detach()
    return [int(p["id"]) for p in info.get("processInfo", []) if "id" in p]


class _Watchdog:
    """Kills the Chromium processes when the render deadline expires."""

    def __init__(self, pids: list[int], seconds: float) -> None:
        self._pids = pids
        self.fired = False
        self._timer = threading.Timer(max(seconds, 0.0), self._fire)
        self._timer.daemon = True

    def _fire(self) -> None:
        self.fired = True
        for pid in self._pids:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass

    def start(self) -> None:
        self._timer.start()

    def cancel(self) -> None:
        self._timer.cancel()


def _close_extra_pages(main: Page) -> Callable[[Page], None]:
    def on_page(page: Page) -> None:
        if page is not main:
            try:
                page.close()
            except PlaywrightError:
                pass

    return on_page


def render_url(
    url: str,
    *,
    resolver: Resolver | None = None,
    settings: Settings | None = None,
    browser_launch_args: list[str] | None = None,
    connector: Connector | None = None,
) -> RenderResult:
    """Render ``url`` in a fresh ephemeral Chromium context and return its HTML.

    The entry URL is validated by the guard BEFORE launching the browser (so an
    illegal entry point fails fast with the guard's typed error). Every
    connection Chromium then opens goes through the egress proxy (see module
    docstring). Image, media and font requests are aborted for economy.

    After navigation the page waits for ``domcontentloaded`` and then
    ``networkidle``, each capped at ``settings.render_networkidle_timeout_ms``
    (SPEC §3.4, default 15 s). A networkidle timeout is NOT fatal: whatever
    loaded is captured anyway via ``page.content()``. The whole render is bound
    by ``settings.render_total_timeout_s`` (:class:`Timeout`) and the DOM by
    ``settings.max_body_bytes`` (:class:`TooLarge`).

    ``resolver`` is forwarded to the guard (tests inject a deterministic one).
    ``connector`` opens the proxy's upstream connection for a guard-validated
    destination; production uses the validated IP, tests map it to loopback.
    ``browser_launch_args`` is an operational escape hatch forwarded to
    Chromium's command line (sandbox flags under hardened CI, or
    ``--host-resolver-rules`` in the browser tests); the egress flags are
    always appended last, so it cannot turn the proxy off.
    """
    settings = settings or get_settings()
    started = time.monotonic()
    deadline = started + settings.render_total_timeout_s

    # Guard the entry point itself before paying for a browser launch. This
    # raises the guard's typed error (InvalidURL/BlockedDestination/...) for an
    # illegal entry URL, exactly like the M1 client.
    validate_url(url, resolver=resolver, settings=settings)

    def remaining_ms(cap: int) -> int:
        return max(1, min(cap, int((deadline - time.monotonic()) * 1000)))

    handler = _make_route_handler(resolver, settings)
    ws_handler = _make_ws_route_handler(resolver, settings)

    watchdog: _Watchdog | None = None
    with (
        EgressProxy(resolver=resolver, settings=settings, connector=connector) as proxy,
        sync_playwright() as playwright,
    ):
        browser = playwright.chromium.launch(
            args=_launch_args(browser_launch_args),
            proxy={"server": proxy.url, "bypass": "<-loopback>"},
        )
        try:
            watchdog = _Watchdog(_browser_pids(browser), deadline - time.monotonic())
            watchdog.start()
            # Fresh ephemeral context per call: no persistence, no downloads,
            # no extra permissions, no service workers.
            context = browser.new_context(
                accept_downloads=False, service_workers="block"
            )
            try:
                # Interception on the CONTEXT (covers popups and every frame),
                # registered before the first page exists.
                context.route_web_socket("**/*", ws_handler)
                context.route("**/*", handler)
                page = context.new_page()
                context.on("page", _close_extra_pages(page))

                response = page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=remaining_ms(_GOTO_TIMEOUT_MS),
                )
                try:
                    page.wait_for_load_state(
                        "networkidle",
                        timeout=remaining_ms(settings.render_networkidle_timeout_ms),
                    )
                except PlaywrightTimeoutError:
                    # networkidle never settled within the cap: proceed with
                    # whatever loaded rather than hanging (SPEC §3.4).
                    pass

                html = page.content()
                final_url = page.url
                status = response.status if response is not None else None
            finally:
                try:
                    context.close()
                except PlaywrightError:
                    pass
        except PlaywrightError as exc:
            if (
                watchdog is not None and watchdog.fired
            ) or time.monotonic() >= deadline:
                raise Timeout(
                    "browser render exceeded its deadline of "
                    f"{settings.render_total_timeout_s}s",
                    details={"url": url, "limit_s": settings.render_total_timeout_s},
                ) from exc
            raise
        finally:
            if watchdog is not None:
                watchdog.cancel()
            try:
                browser.close()
            except PlaywrightError:
                pass

    size = len(html.encode("utf-8"))
    if size > settings.max_body_bytes:
        raise TooLarge(
            f"rendered DOM exceeded limit of {settings.max_body_bytes} bytes",
            details={"limit": settings.max_body_bytes, "bytes": size, "url": url},
        )
    return RenderResult(final_url=final_url, html=html, status=status)
