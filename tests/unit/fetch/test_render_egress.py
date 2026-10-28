"""Browser egress regression tests for the 2026-10-28 security audit.

docs/AUDITORIA_SEGURANCA.md C1 (redirect hops escaped ``page.route``), A4
(popups), M3 (service workers), M4 (browser DNS rebinding) and M2 (no render
deadline, no DOM cap). Every case here failed against the ``page.route``-only
renderer and passes with the egress proxy (``app/fetch/egress_proxy.py``).

Harness: the same as ``test_render.py``. ``FakeResolver`` tells the GUARD a
fixture name is public; ``_route`` wires each loopback fixture both as a
Chromium ``--host-resolver-rules`` MAP (so a connection that bypassed the
proxy would land on a counting fixture) and as the proxy connector (used only
after the guard accepted a destination). The guard itself is used verbatim.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from app.config import Settings
from app.fetch import render
from app.fetch.errors import Timeout, TooLarge
from tests.unit.fetch._local_servers import (
    Reply,
    counting_server,
    scripted_server,
    tcp_accept_counter,
)
from tests.unit.fetch._resolvers import FakeResolver, loopback_connector

pytestmark = [pytest.mark.browser, pytest.mark.allow_network]

_PUBLIC_IP = "93.184.216.34"
_HOST = "rendered.test"
_META = "169.254.169.254"


def _port(base_url: str) -> int:
    return int(base_url.rsplit(":", 1)[1])


def _route(*mappings: str) -> dict[str, Any]:
    return {
        "browser_launch_args": [f"--host-resolver-rules={','.join(mappings)}"],
        "connector": loopback_connector(mappings),
    }


def _html(body: str) -> Reply:
    return Reply(body=f"<!doctype html><html><body>{body}</body></html>".encode())


def _redirect(location: str) -> Reply:
    return Reply(status=302, headers=(("Location", location),))


def test_navigation_redirect_to_metadata_is_refused() -> None:
    """C1: the entry page 302s to the metadata IP; the hop must not connect."""
    with (
        counting_server() as meta,
        scripted_server(
            {"/redir": _redirect(f"http://{_META}/latest/meta-data")}
        ) as page,
    ):
        result = render.render_url(
            f"http://{_HOST}/redir",
            resolver=FakeResolver({_HOST: [_PUBLIC_IP]}),
            **_route(
                f"MAP {_HOST} 127.0.0.1:{_port(page.base_url)}",
                f"MAP {_META} 127.0.0.1:{_port(meta.base_url)}",
            ),
        )
    assert meta.counter.count == 0
    assert "COUNTED" not in result.html


def test_subrequest_redirect_to_metadata_is_refused() -> None:
    """C1: a same-origin fetch() whose response 302s to the metadata IP."""
    script = (
        "<div id=out>wait</div><script>fetch('/sub').then(r=>r.text())"
        ".then(t=>{document.getElementById('out').textContent=t})"
        ".catch(e=>{document.getElementById('out').textContent='ERR'});</script>"
    )
    with (
        counting_server() as meta,
        scripted_server(
            {
                "/": _html(script),
                "/sub": _redirect(f"http://{_META}/latest/meta-data"),
            }
        ) as page,
    ):
        result = render.render_url(
            f"http://{_HOST}/",
            resolver=FakeResolver({_HOST: [_PUBLIC_IP]}),
            **_route(
                f"MAP {_HOST} 127.0.0.1:{_port(page.base_url)}",
                f"MAP {_META} 127.0.0.1:{_port(meta.base_url)}",
            ),
        )
    assert meta.counter.count == 0
    assert "COUNTED" not in result.html


def test_popup_and_iframe_to_metadata_are_refused() -> None:
    """A4: window.open() and an iframe aimed at the metadata IP never connect."""
    body = (
        f"<iframe src='http://{_META}/frame'></iframe>"
        f"<script>window.open('http://{_META}/popup');</script><p>main</p>"
    )
    with counting_server() as meta, scripted_server({"/": _html(body)}) as page:
        render.render_url(
            f"http://{_HOST}/",
            resolver=FakeResolver({_HOST: [_PUBLIC_IP]}),
            **_route(
                f"MAP {_HOST} 127.0.0.1:{_port(page.base_url)}",
                f"MAP {_META} 127.0.0.1:{_port(meta.base_url)}",
            ),
        )
    assert meta.counter.count == 0


def test_preconnect_to_private_name_opens_no_connection() -> None:
    """Connection-level features (preconnect, dns-prefetch) never reach a
    private address: no ``page.route`` hook sees them, only the proxy does."""
    body = (
        "<link rel='preconnect' href='http://internal.test'>"
        "<link rel='dns-prefetch' href='http://internal.test'>"
        "<link rel='prefetch' href='http://internal.test/next'>"
        "<p>main</p>"
    )
    with tcp_accept_counter() as internal, scripted_server({"/": _html(body)}) as page:
        render.render_url(
            f"http://{_HOST}/",
            resolver=FakeResolver({_HOST: [_PUBLIC_IP], "internal.test": ["10.0.0.7"]}),
            **_route(
                f"MAP {_HOST} 127.0.0.1:{_port(page.base_url)}",
                f"MAP internal.test 127.0.0.1:{_port(internal.base_url)}",
            ),
        )
    assert internal.counter.count == 0


def test_browser_dns_rebinding_window_is_closed() -> None:
    """M4: the guard sees a public IP for ``rebind.test`` but Chromium's own
    resolver would answer a private one (the rebinding answer). The request
    must go to the guard-validated destination, never to the private one."""
    body = "<script>fetch('http://rebind.test/data').catch(()=>{});</script><p>x</p>"
    with (
        counting_server() as private,
        counting_server() as public,
        scripted_server({"/": _html(body)}) as page,
    ):
        rules = (
            f"MAP {_HOST} 127.0.0.1:{_port(page.base_url)}",
            f"MAP rebind.test 127.0.0.1:{_port(private.base_url)}",
        )
        connector_rules = (
            rules[0],
            f"MAP rebind.test 127.0.0.1:{_port(public.base_url)}",
        )
        render.render_url(
            f"http://{_HOST}/",
            resolver=FakeResolver({_HOST: [_PUBLIC_IP], "rebind.test": [_PUBLIC_IP]}),
            browser_launch_args=[f"--host-resolver-rules={','.join(rules)}"],
            connector=loopback_connector(connector_rules),
        )
    assert private.counter.count == 0
    assert public.counter.count >= 1


def test_service_worker_cannot_reach_metadata() -> None:
    """M3: service workers are blocked; a SW fetch to metadata never connects.

    The audit could not activate a SW within the render either (inconclusive
    before the fix); this stays as a regression guard for the new
    ``service_workers="block"`` context option.
    """
    sw = (
        "self.addEventListener('install',e=>self.skipWaiting());"
        "self.addEventListener('activate',e=>e.waitUntil(clients.claim()));"
        f"self.addEventListener('fetch',e=>e.respondWith(fetch('http://{_META}/sw')));"
    )
    page_body = (
        "<div id=out>wait</div><script>navigator.serviceWorker.register('/sw.js')"
        ".then(()=>navigator.serviceWorker.ready).then(()=>fetch('/via-sw'))"
        ".catch(()=>{});</script>"
    )
    routes = {
        "/": _html(page_body),
        "/sw.js": Reply(
            body=sw.encode(), headers=(("Content-Type", "text/javascript"),)
        ),
    }
    with counting_server() as meta, scripted_server(routes) as page:
        kwargs = _route(
            f"MAP {_HOST} 127.0.0.1:{_port(page.base_url)}",
            f"MAP {_META} 127.0.0.1:{_port(meta.base_url)}",
        )
        kwargs["browser_launch_args"].append(
            f"--unsafely-treat-insecure-origin-as-secure=http://{_HOST}"
        )
        render.render_url(
            f"http://{_HOST}/",
            resolver=FakeResolver({_HOST: [_PUBLIC_IP]}),
            **kwargs,
        )
    assert meta.counter.count == 0


def test_render_deadline_kills_a_busy_page() -> None:
    """M2: a JS busy loop used to hang ``page.content()`` forever."""
    body = "<p>x</p><script>setTimeout(()=>{while(true){}},300)</script>"
    with scripted_server({"/": _html(body)}) as page:
        started = time.monotonic()
        with pytest.raises(Timeout):
            render.render_url(
                f"http://{_HOST}/",
                resolver=FakeResolver({_HOST: [_PUBLIC_IP]}),
                settings=Settings(render_total_timeout_s=6),
                **_route(f"MAP {_HOST} 127.0.0.1:{_port(page.base_url)}"),
            )
    assert time.monotonic() - started < 30


def test_rendered_dom_is_capped() -> None:
    """M2: a tiny page that builds a huge DOM is refused with TooLarge."""
    body = (
        "<div id=o></div><script>document.getElementById('o').textContent="
        "'A'.repeat(1<<20);</script>"
    )
    with scripted_server({"/": _html(body)}) as page:
        with pytest.raises(TooLarge):
            render.render_url(
                f"http://{_HOST}/",
                resolver=FakeResolver({_HOST: [_PUBLIC_IP]}),
                settings=Settings(max_body_bytes=256 * 1024),
                **_route(f"MAP {_HOST} 127.0.0.1:{_port(page.base_url)}"),
            )
