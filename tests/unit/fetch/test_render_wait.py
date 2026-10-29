"""Stable-text wait and no-content blocking of the renderer (M10b, SPEC §3.4).

The render used to wait for ``networkidle`` (capped at 15 s), which pages with
analytics beacons, long polling or open streams never reach. It now stops when
the main text is stable. Harness as in ``test_render_egress.py``: the guard is
used verbatim with a ``FakeResolver``; every fixture host is mapped to a
loopback server both in Chromium's resolver rules and in the egress proxy's
connector, so a request that was not blocked is COUNTED by the fixture.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from app.config import Settings
from app.fetch import render
from tests.unit.fetch._resolvers import FakeResolver, loopback_connector

pytestmark = [pytest.mark.browser, pytest.mark.allow_network]

_PUBLIC_IP = "93.184.216.34"
_HOST = "rendered.test"
_LONG = "<p>" + "Documentation text about the API and its parameters. " * 20 + "</p>"


class _Hits:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.paths: list[str] = []

    def add(self, path: str) -> None:
        with self.lock:
            self.paths.append(path)

    def count(self, path: str) -> int:
        with self.lock:
            return self.paths.count(path)


@contextmanager
def _server(pages: dict[str, str]) -> Iterator[tuple[int, _Hits]]:
    hits = _Hits()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 — stdlib API.
            hits.add(self.path)
            if self.path in ("/hang", "/events"):
                time.sleep(20)
                return
            body = pages.get(self.path, "").encode()
            ctype = "text/javascript" if self.path.endswith(".js") else "text/html"
            self.send_response(200 if self.path in pages else 404)
            self.send_header("Content-Type", f"{ctype}; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802 — stdlib API.
            hits.add(self.path)
            self.send_response(204)
            self.end_headers()

        def log_message(self, *_args: object) -> None:
            return

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield int(httpd.server_address[1]), hits
    finally:
        httpd.shutdown()
        httpd.server_close()


def _render(path: str, ports: dict[str, int], **kwargs: Any) -> render.RenderResult:
    rules = [f"MAP {host} 127.0.0.1:{port}" for host, port in ports.items()]
    return render.render_url(
        f"http://{_HOST}{path}",
        resolver=FakeResolver({host: [_PUBLIC_IP] for host in ports}),
        browser_launch_args=[f"--host-resolver-rules={','.join(rules)}"],
        connector=loopback_connector(rules),
        **kwargs,
    )


def test_busy_network_does_not_hold_the_render() -> None:
    """A long poll that never answers plus a beacon every 200 ms: networkidle
    never came (15 s); the stable-text wait returns well inside its 5 s cap,
    and the beacons are aborted before they leave the browser."""
    page = (
        f"<html><body><main>{_LONG}</main><script>fetch('/hang');"
        "setInterval(function(){navigator.sendBeacon('/beacon','x')},200);"
        "</script></body></html>"
    )
    with _server({"/": page}) as (port, hits):
        started = time.monotonic()
        result = _render("/", {_HOST: port})
        elapsed = time.monotonic() - started
    assert "Documentation text about the API" in result.html
    assert elapsed < 10
    assert hits.count("/beacon") == 0


def test_late_spa_content_is_waited_for() -> None:
    """Short shell text while a request hangs: the wait keeps going until the
    content arrives (0.8 s later) and is stable, then captures it."""
    page = (
        "<html><body><main id=root>loading</main><script>"
        "setTimeout(function(){document.getElementById('root').innerHTML="
        f"{_LONG!r};}},800);fetch('/hang');</script></body></html>"
    )
    with _server({"/": page}) as (port, _hits):
        result = _render("/", {_HOST: port})
    assert "Documentation text about the API" in result.html


def test_short_quiet_page_returns_without_waiting_for_the_cap() -> None:
    settings = Settings(render_stable_cap_ms=30_000)
    with _server({"/": "<html><body><p>tiny</p></body></html>"}) as (port, _h):
        started = time.monotonic()
        result = _render("/", {_HOST: port}, settings=settings)
        elapsed = time.monotonic() - started
    assert "tiny" in result.html
    assert elapsed < 15  # far below the 30 s cap: no request was in flight


def test_analytics_and_event_streams_are_blocked_other_scripts_are_not() -> None:
    page = (
        f"<html><body><main>{_LONG}</main>"
        '<script src="http://www.googletagmanager.com/gtm.js"></script>'
        '<script src="http://cdn.test/app.js"></script>'
        "<script>try{new EventSource('/events')}catch(e){}</script>"
        "</body></html>"
    )
    with (
        _server({"/": page}) as (port, hits),
        _server({"/gtm.js": "1;", "/app.js": "1;"}) as (scripts_port, script_hits),
    ):
        _render(
            "/",
            {
                _HOST: port,
                "www.googletagmanager.com": scripts_port,
                "cdn.test": scripts_port,
            },
        )
    assert script_hits.count("/gtm.js") == 0
    assert script_hits.count("/app.js") == 1
    assert hits.count("/events") == 0
