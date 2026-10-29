"""Unit tests of the guard-enforcing egress proxy (loopback only, no browser)."""

from __future__ import annotations

import socket
from typing import Any

import pytest

from app.config import Settings
from app.fetch.egress_proxy import EgressProxy
from app.fetch.guard import GuardResult
from tests.unit.fetch._local_servers import Reply, scripted_server
from tests.unit.fetch._resolvers import FakeResolver

pytestmark = [pytest.mark.allow_network]

_PUBLIC_IP = "93.184.216.34"


class _Recorder:
    """Connector that records guards and connects to a fixed loopback port."""

    def __init__(self, port: int | None) -> None:
        self.port = port
        self.guards: list[GuardResult] = []

    def __call__(self, guard: GuardResult, timeout: float) -> socket.socket:
        self.guards.append(guard)
        if self.port is None:
            raise OSError("no upstream")
        return socket.create_connection(("127.0.0.1", self.port), timeout=timeout)


def _exchange(proxy_url: str, raw: bytes) -> bytes:
    port = int(proxy_url.rsplit(":", 1)[1])
    with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
        sock.sendall(raw)
        chunks = []
        while True:
            try:
                data = sock.recv(65536)
            except TimeoutError:
                break
            if not data:
                break
            chunks.append(data)
            if raw.startswith(b"CONNECT") and b"\r\n\r\n" in b"".join(chunks):
                break
        return b"".join(chunks)


def _proxy(resolver: Any, connector: Any) -> EgressProxy:
    return EgressProxy(resolver=resolver, settings=Settings(), connector=connector)


@pytest.mark.parametrize(
    "request_bytes",
    [
        b"CONNECT 169.254.169.254:443 HTTP/1.1\r\nHost: x\r\n\r\n",
        b"CONNECT internal.test:443 HTTP/1.1\r\n\r\n",
        b"CONNECT good.test:22 HTTP/1.1\r\n\r\n",
        b"GET http://169.254.169.254/latest/meta-data HTTP/1.1\r\nHost: x\r\n\r\n",
        b"GET http://internal.test/ HTTP/1.1\r\nHost: internal.test\r\n\r\n",
        b"GET http://[::ffff:10.0.0.1]/ HTTP/1.1\r\n\r\n",
    ],
)
def test_blocked_destinations_get_403_and_never_connect(request_bytes: bytes) -> None:
    connector = _Recorder(port=None)
    resolver = FakeResolver({"internal.test": ["10.0.0.7"], "good.test": [_PUBLIC_IP]})
    with _proxy(resolver, connector) as proxy:
        reply = _exchange(proxy.url, request_bytes)
    assert reply.startswith(b"HTTP/1.1 403")
    # No detail about the destination leaks back to the page.
    assert b"10.0.0" not in reply and b"169.254" not in reply
    assert connector.guards == []
    assert proxy.refused


@pytest.mark.parametrize(
    "request_bytes",
    [
        b"GET / HTTP/1.1\r\nHost: good.test\r\n\r\n",
        b"GET https://good.test/ HTTP/1.1\r\n\r\n",
        b"NONSENSE\r\n\r\n",
        b"CONNECT good.test:443/x HTTP/1.1\r\n\r\n",
    ],
)
def test_malformed_requests_are_refused(request_bytes: bytes) -> None:
    connector = _Recorder(port=None)
    with _proxy(FakeResolver({"good.test": [_PUBLIC_IP]}), connector) as proxy:
        reply = _exchange(proxy.url, request_bytes)
    assert reply.startswith(b"HTTP/1.1 400")
    assert connector.guards == []


def test_allowed_http_request_is_forwarded_origin_form_with_close() -> None:
    routes = {"/doc?x=1": Reply(body=b"HELLO", headers=(("Connection", "keep-alive"),))}
    with scripted_server(routes) as origin:
        connector = _Recorder(port=int(origin.base_url.rsplit(":", 1)[1]))
        with _proxy(FakeResolver({"good.test": [_PUBLIC_IP]}), connector) as proxy:
            reply = _exchange(
                proxy.url,
                b"GET http://good.test/doc?x=1 HTTP/1.1\r\nHost: good.test\r\n"
                b"Proxy-Connection: keep-alive\r\n\r\n",
            )
    assert reply.startswith(b"HTTP/1.0 200") or reply.startswith(b"HTTP/1.1 200")
    assert reply.endswith(b"HELLO")
    assert b"Connection: close" in reply
    assert b"keep-alive" not in reply.lower()
    assert origin.counter.paths == ["/doc?x=1"]
    # The proxy connected on the guard-validated IP, never on a re-resolved name.
    assert [g.validated_ip for g in connector.guards] == [_PUBLIC_IP]


def test_connect_tunnel_goes_only_to_the_validated_ip() -> None:
    with scripted_server({"/": Reply(body=b"TUNNELED")}) as origin:
        connector = _Recorder(port=int(origin.base_url.rsplit(":", 1)[1]))
        with _proxy(FakeResolver({"good.test": [_PUBLIC_IP]}), connector) as proxy:
            port = int(proxy.url.rsplit(":", 1)[1])
            with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
                sock.sendall(b"CONNECT good.test:443 HTTP/1.1\r\n\r\n")
                established = sock.recv(1024)
                sock.sendall(b"GET / HTTP/1.1\r\nHost: good.test\r\n\r\n")
                tunneled = b""
                while b"TUNNELED" not in tunneled:
                    data = sock.recv(65536)
                    if not data:
                        break
                    tunneled += data
    assert established.startswith(b"HTTP/1.1 200")
    assert b"TUNNELED" in tunneled
    assert [(g.validated_ip, g.port) for g in connector.guards] == [(_PUBLIC_IP, 443)]


def test_upstream_connect_failure_is_502() -> None:
    connector = _Recorder(port=None)
    with _proxy(FakeResolver({"good.test": [_PUBLIC_IP]}), connector) as proxy:
        reply = _exchange(proxy.url, b"GET http://good.test/ HTTP/1.1\r\n\r\n")
    assert reply.startswith(b"HTTP/1.1 502")
