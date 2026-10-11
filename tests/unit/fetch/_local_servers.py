"""Threaded loopback HTTP servers for the browser render tests (NOT collected).

This module has no ``test_`` prefix on purpose: pytest never collects it. It
offers small ``http.server`` based servers bound to ``127.0.0.1:0`` that the
``@pytest.mark.browser`` render tests drive Chromium against:

* :func:`spa_server` — serves an SPA shell whose main content only appears
  after a tiny inline script injects text into an empty ``#root``; proves the
  browser actually executed JS.
* :func:`page_requesting` — serves a page whose HTML triggers a sub-request to
  an arbitrary target URL (an ``<img>`` and an inline ``fetch()``); used to
  prove the guard aborts a sub-request to a blocked destination.
* :func:`page_opening_websocket` — serves a page whose inline script opens a
  ``new WebSocket(target_url)``; used to prove the guard refuses a WebSocket
  connection to a blocked destination. A WebSocket upgrade reaches the plain
  ``counting_server`` as a ``GET`` carrying ``Upgrade: websocket`` and is
  counted even though the handshake never completes — so a ZERO count proves
  the connection never reached the destination.
* :func:`counting_server` — counts every connection/HTTP hit it receives and
  exposes the count; standing in for ``169.254.169.254`` / ``10.0.0.1`` it
  proves ZERO hits when the guard blocks the destination, and is also reused to
  assert that economy (image) requests are aborted.

Each helper returns a :class:`LocalServer` context manager yielding the server's
``http://127.0.0.1:<port>`` base URL and, where relevant, a live hit counter.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


@dataclass
class HitCounter:
    """Thread-safe counter of HTTP hits, optionally split by path."""

    _lock: threading.Lock = field(default_factory=threading.Lock)
    total: int = 0
    paths: list[str] = field(default_factory=list)

    def record(self, path: str) -> None:
        with self._lock:
            self.total += 1
            self.paths.append(path)

    @property
    def count(self) -> int:
        with self._lock:
            return self.total


@dataclass
class LocalServer:
    """A running loopback server plus its base URL and optional hit counter."""

    base_url: str
    counter: HitCounter


def _serve(
    handler_cls: type[BaseHTTPRequestHandler],
) -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    host, port = server.server_address[0], server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://{host}:{port}"


@contextmanager
def _run(
    handler_cls: type[BaseHTTPRequestHandler], counter: HitCounter
) -> Iterator[LocalServer]:
    server, base_url = _serve(handler_cls)
    try:
        yield LocalServer(base_url=base_url, counter=counter)
    finally:
        server.shutdown()
        server.server_close()


def _html_handler(body: bytes, counter: HitCounter) -> type[BaseHTTPRequestHandler]:
    """Build a handler serving ``body`` as text/html for any GET and counting hits."""

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler API.
            counter.record(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args: object) -> None:  # silence test noise
            return

    return _Handler


@contextmanager
def spa_server(injected_text: str) -> Iterator[LocalServer]:
    """Serve an SPA shell that injects ``injected_text`` into an empty #root via JS."""
    counter = HitCounter()
    page = (
        "<!doctype html><html><head><title>spa</title></head><body>"
        '<div id="root"></div>'
        "<script>"
        'document.getElementById("root").textContent = '
        f"{injected_text!r};"
        "</script>"
        "</body></html>"
    ).encode()
    with _run(_html_handler(page, counter), counter) as server:
        yield server


@contextmanager
def page_requesting(target_url: str) -> Iterator[LocalServer]:
    """Serve a page that triggers a sub-request to ``target_url`` (img + fetch)."""
    counter = HitCounter()
    page = (
        "<!doctype html><html><head><title>req</title></head><body>"
        "<p>main</p>"
        f'<img src="{target_url}" alt="x">'
        "<script>"
        f"fetch({target_url!r}).catch(function(){{}});"
        "</script>"
        "</body></html>"
    ).encode()
    with _run(_html_handler(page, counter), counter) as server:
        yield server


@contextmanager
def page_opening_websocket(target_url: str) -> Iterator[LocalServer]:
    """Serve a page whose script opens ``new WebSocket(target_url)``."""
    counter = HitCounter()
    page = (
        "<!doctype html><html><head><title>ws</title></head><body>"
        "<p>main</p>"
        "<script>"
        f"try{{var w=new WebSocket({target_url!r});}}catch(e){{}}"
        "</script>"
        "</body></html>"
    ).encode()
    with _run(_html_handler(page, counter), counter) as server:
        yield server


@contextmanager
def counting_server() -> Iterator[LocalServer]:
    """Serve a tiny 200 for any GET while counting every hit (metadata stand-in)."""
    counter = HitCounter()
    body = b"COUNTED"

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler API.
            counter.record(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args: object) -> None:
            return

    with _run(_Handler, counter) as server:
        yield server
