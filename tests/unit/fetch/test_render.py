"""Browser render tests — guard-enforced request interception (SPEC §3.4).

EVERY test here is marked BOTH ``@pytest.mark.browser`` (needs a real Chromium;
run in the dedicated CI step) AND ``@pytest.mark.allow_network`` (drives Chromium
against loopback fixture servers, opting out of the fetch-suite no-network guard).

How loopback is reached WITHOUT weakening the guard
---------------------------------------------------
The guard (``app.fetch.guard.validate_url``) blocks loopback/private/metadata
IPs unconditionally — a resolver cannot make ``127.0.0.1`` acceptable. So the
fixture pages are served under PUBLIC-looking hostnames and two independent maps
are wired:

* the injected :class:`FakeResolver` maps the fixture hostname to a PUBLIC IP,
  so the guard VALIDATES the navigation/same-origin requests and lets them
  continue; and
* Chromium's ``--host-resolver-rules`` (passed via ``browser_launch_args``) maps
  that same hostname — and, crucially, the BLOCKED metadata/private hosts — to
  the loopback fixture ports, so Chromium would actually CONNECT to the counting
  server IF the guard ever let a blocked request through.

That second map is what makes the central assertion meaningful: the counting
server IS reachable from Chromium; the only thing keeping its hit count at ZERO
is the guard aborting the request at ``page.route`` BEFORE Chromium connects.
The guard itself is used verbatim — no IP/port policy is monkeypatched.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.config import Settings
from app.fetch import render
from tests.unit.fetch._local_servers import (
    counting_server,
    page_opening_websocket,
    page_requesting,
    spa_server,
)
from tests.unit.fetch._resolvers import FakeResolver, loopback_connector

pytestmark = [pytest.mark.browser, pytest.mark.allow_network]

# A routable public IP the guard accepts (same value the M1 client tests use).
# The injected resolver maps fixture hosts to it so the guard VALIDATES them;
# Chromium's host-resolver-rules then redirect the actual connection to loopback.
_PUBLIC_IP = "93.184.216.34"
_FIXTURE_HOST = "rendered.test"


def _port_of(base_url: str) -> int:
    """Extract the port from an ``http://127.0.0.1:PORT`` base URL."""
    return int(base_url.rsplit(":", 1)[1])


def _host_rules(*mappings: str) -> list[str]:
    """Build the Chromium ``--host-resolver-rules`` launch arg from MAP rules."""
    return [f"--host-resolver-rules={','.join(mappings)}"]


def _route(*mappings: str) -> dict[str, Any]:
    """render_url kwargs wiring each ``MAP host 127.0.0.1:port`` rule TWICE.

    * as Chromium ``--host-resolver-rules``: if any connection ever bypassed
      the egress proxy, Chromium would resolve the name itself and land on the
      loopback fixture (so a bypass is counted, not silently lost); and
    * as the egress proxy ``connector``: AFTER the guard validates a
      destination (public IP via ``FakeResolver``), the proxy connects to the
      mapped loopback port instead of the public IP. A destination the guard
      rejects never reaches the connector.
    """
    return {
        "browser_launch_args": _host_rules(*mappings),
        "connector": loopback_connector(mappings),
    }


def test_spa_is_rendered_with_browser() -> None:
    """The browser executes JS: content injected into an empty #root appears.

    Proves render_url returns the POST-render DOM (page.content()), which is the
    whole point of browser rendering, and that the provenance contract is
    exposed so the caller can set render_mode='browser' + the warning.
    """
    injected = "HELLO-FROM-SPA-JS"
    with spa_server(injected) as server:
        resolver = FakeResolver({_FIXTURE_HOST: [_PUBLIC_IP]})
        entry = f"http://{_FIXTURE_HOST}/"
        result = render.render_url(
            entry,
            resolver=resolver,
            **_route(f"MAP {_FIXTURE_HOST} 127.0.0.1:{_port_of(server.base_url)}"),
        )

    assert injected in result.html
    assert result.status == 200
    # Provenance contract the M5 caller relies on.
    assert render.RENDERED_WITH_BROWSER_WARNING_CODE == "rendered_with_browser"


@pytest.mark.parametrize("blocked_host", ["169.254.169.254", "10.0.0.1"])
def test_subrequest_to_blocked_host_is_aborted(blocked_host: str) -> None:
    """CENTRAL SSRF proof: a sub-request to metadata/private IP never connects.

    The fixture page (served under a public hostname the guard accepts) triggers
    an ``<img>`` and a ``fetch()`` to ``http://<blocked_host>/latest/meta-data``.
    Chromium's host-resolver-rules point ``<blocked_host>`` at the loopback
    counting server, so the request WOULD reach the counter if the guard let it
    through. It does not: the guard rejects the private/metadata IP at
    ``page.route`` and the handler aborts it. We assert the counter is ZERO.
    """
    with (
        counting_server() as metadata,
        page_requesting(f"http://{blocked_host}/latest/meta-data") as page,
    ):
        resolver = FakeResolver({_FIXTURE_HOST: [_PUBLIC_IP]})
        entry = f"http://{_FIXTURE_HOST}/"
        meta_port = _port_of(metadata.base_url)
        page_port = _port_of(page.base_url)
        render.render_url(
            entry,
            resolver=resolver,
            **_route(
                f"MAP {_FIXTURE_HOST} 127.0.0.1:{page_port}",
                # If the guard FAILED to block, Chromium would connect here.
                f"MAP {blocked_host} 127.0.0.1:{meta_port}",
            ),
        )

    # The guard aborted every attempt before Chromium connected.
    assert metadata.counter.count == 0


def test_allowed_subrequest_is_not_aborted() -> None:
    """Interception is not a blanket block: a guard-allowed sub-request flows.

    A second public-looking host (resolving to a public IP, so the guard allows
    it) serves a counting endpoint. The fixture page fetches it; because the
    guard validates it OK the handler calls route.continue_() and the request
    reaches the counting server (count >= 1).
    """
    allowed_host = "allowed-sub.test"
    with (
        counting_server() as sub,
        page_requesting(f"http://{allowed_host}/data.json") as page,
    ):
        resolver = FakeResolver(
            {_FIXTURE_HOST: [_PUBLIC_IP], allowed_host: [_PUBLIC_IP]}
        )
        entry = f"http://{_FIXTURE_HOST}/"
        sub_port = _port_of(sub.base_url)
        page_port = _port_of(page.base_url)
        render.render_url(
            entry,
            resolver=resolver,
            **_route(
                f"MAP {_FIXTURE_HOST} 127.0.0.1:{page_port}",
                f"MAP {allowed_host} 127.0.0.1:{sub_port}",
            ),
        )

    # The fetch() to the allowed host was continued and reached the server. The
    # <img> to the same host is aborted for economy, so exactly the fetch hit
    # lands — assert the allowed request was NOT indiscriminately blocked.
    assert sub.counter.count >= 1


def test_image_subrequest_is_aborted_for_economy() -> None:
    """image/media/font requests are aborted for bandwidth economy (SPEC §3.4).

    The page points ONLY an <img> at a guard-allowed public host mapped to the
    loopback counter. Even though the guard WOULD allow the host, the handler
    aborts it because its resource_type is 'image'. The counter stays ZERO.
    """
    img_host = "img-economy.test"
    img_url = f"http://{img_host}/pixel.png"
    page_body = (
        "<!doctype html><html><head><title>img</title></head><body>"
        "<p>main</p>"
        f'<img src="{img_url}" alt="x">'
        "</body></html>"
    ).encode()

    # Build a page server that serves exactly this image-only body.
    from tests.unit.fetch._local_servers import HitCounter, _html_handler, _run

    page_counter = HitCounter()
    with (
        counting_server() as img,
        _run(_html_handler(page_body, page_counter), page_counter) as page,
    ):
        resolver = FakeResolver({_FIXTURE_HOST: [_PUBLIC_IP], img_host: [_PUBLIC_IP]})
        entry = f"http://{_FIXTURE_HOST}/"
        img_port = _port_of(img.base_url)
        page_port = _port_of(page.base_url)
        render.render_url(
            entry,
            resolver=resolver,
            **_route(
                f"MAP {_FIXTURE_HOST} 127.0.0.1:{page_port}",
                f"MAP {img_host} 127.0.0.1:{img_port}",
            ),
        )

    assert img.counter.count == 0


@pytest.mark.parametrize("blocked_host", ["169.254.169.254", "10.0.0.1"])
def test_websocket_to_blocked_host_is_refused(blocked_host: str) -> None:
    """CENTRAL WS SSRF proof: a WebSocket to metadata/private IP never connects.

    WebSocket connections are NOT seen by ``page.route``; they are intercepted
    separately via ``page.route_web_socket``. The fixture page opens
    ``new WebSocket("ws://<blocked_host>/")``. Chromium's host-resolver-rules
    point ``<blocked_host>`` at the loopback counting server, so the upgrade
    WOULD reach the counter if the guard let it through. It does not: the WS
    handler rewrites ws→http, the guard rejects the private/metadata IP, and the
    handler returns without ``connect_to_server()``. We assert the counter is
    ZERO. Without the WS route this count is 1 (bypass), so the test is not
    vacuous.
    """
    with (
        counting_server() as metadata,
        page_opening_websocket(f"ws://{blocked_host}/socket") as page,
    ):
        resolver = FakeResolver({_FIXTURE_HOST: [_PUBLIC_IP]})
        entry = f"http://{_FIXTURE_HOST}/"
        meta_port = _port_of(metadata.base_url)
        page_port = _port_of(page.base_url)
        render.render_url(
            entry,
            resolver=resolver,
            **_route(
                f"MAP {_FIXTURE_HOST} 127.0.0.1:{page_port}",
                # If the guard FAILED to block, Chromium would connect here.
                f"MAP {blocked_host} 127.0.0.1:{meta_port}",
            ),
        )

    # The WS handler refused the connection before Chromium reached the host.
    assert metadata.counter.count == 0


def test_allowed_websocket_is_not_refused() -> None:
    """Interception is not a blanket WS block: a guard-allowed WS connects.

    A public-looking host (resolving to a public IP, so the guard allows it)
    stands in for the WS endpoint. The page opens a WebSocket to it; because the
    guard validates it OK the handler calls ``connect_to_server()`` and the
    upgrade reaches the counting server (count >= 1). This proves the WS block
    is destination-specific, and keeps the blocked-case test honest.
    """
    allowed_host = "allowed-ws.test"
    with (
        counting_server() as sub,
        page_opening_websocket(f"ws://{allowed_host}/socket") as page,
    ):
        resolver = FakeResolver(
            {_FIXTURE_HOST: [_PUBLIC_IP], allowed_host: [_PUBLIC_IP]}
        )
        entry = f"http://{_FIXTURE_HOST}/"
        sub_port = _port_of(sub.base_url)
        page_port = _port_of(page.base_url)
        render.render_url(
            entry,
            resolver=resolver,
            **_route(
                f"MAP {_FIXTURE_HOST} 127.0.0.1:{page_port}",
                f"MAP {allowed_host} 127.0.0.1:{sub_port}",
            ),
        )

    assert sub.counter.count >= 1


def test_illegal_entry_url_is_rejected_before_browser() -> None:
    """A blocked entry URL raises the guard's typed error, no browser needed."""
    from app.fetch.errors import BlockedDestination

    resolver = FakeResolver({})
    with pytest.raises(BlockedDestination):
        render.render_url("http://10.0.0.1/", resolver=resolver, settings=Settings())
