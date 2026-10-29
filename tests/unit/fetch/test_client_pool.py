"""Per-hostname keep-alive pool of the guarded client (M10b efficiency).

Connection reuse must never weaken the SSRF defense (SPEC §3.1, decision D1):

* every request and every redirect hop is still validated by the guard;
* a request goes to the IP the guard validated FOR THAT REQUEST — a warm
  connection to an earlier IP is never used after the name moved (DNS
  rebinding);
* two hostnames on one IP never share a connection (the TLS session was
  authenticated, SNI and certificate, for one name only);
* cookies never carry state from one ``fetch`` call to the next.

Harness: loopback servers stand in for public IPs. The guard sees a
``FakeResolver`` answer (a public literal) and is used verbatim; only
httpcore's TCP connect is redirected from the fake public IP to the loopback
port, and every connect is recorded, so the tests observe exactly which
validated IP each connection was opened for.
"""

from __future__ import annotations

import http.server
import ssl
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import httpcore
import pytest
import trustme

from app.fetch import client as client_mod
from app.fetch.errors import BlockedDestination
from tests.unit.fetch._resolvers import FakeResolver, SequenceResolver

pytestmark = pytest.mark.allow_network

IP_A = "93.184.216.34"
IP_B = "93.184.216.35"
PRIVATE_IP = "10.0.0.1"


@dataclass
class Seen:
    """What one loopback server observed."""

    lock: threading.Lock = field(default_factory=threading.Lock)
    connections: int = 0
    requests: list[dict[str, str | None]] = field(default_factory=list)
    sni_by_socket: dict[int, str | None] = field(default_factory=dict)


def _handler(seen: Seen, routes: dict[str, tuple[int, dict[str, str]]]) -> Any:
    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def setup(self) -> None:
            super().setup()
            with seen.lock:
                seen.connections += 1

        def do_GET(self) -> None:  # noqa: N802 — stdlib API.
            with seen.lock:
                seen.requests.append(
                    {
                        "path": self.path,
                        "host": self.headers.get("Host"),
                        "cookie": self.headers.get("Cookie"),
                        "sni": seen.sni_by_socket.get(id(self.request)),
                    }
                )
            status, headers = routes.get(self.path, (200, {}))
            body = b"<html><body>ok</body></html>"
            self.send_response(status)
            self.send_header("Content-Type", "text/html")
            for name, value in headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args: object) -> None:
            return

    return Handler


@dataclass
class Server:
    port: int
    seen: Seen


@pytest.fixture
def make_server() -> Iterator[Any]:
    servers: list[http.server.ThreadingHTTPServer] = []

    def make(
        routes: dict[str, tuple[int, dict[str, str]]] | None = None,
        tls: ssl.SSLContext | None = None,
    ) -> Server:
        seen = Seen()
        httpd = http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0), _handler(seen, routes or {})
        )
        httpd.daemon_threads = True
        if tls is not None:

            def on_sni(sock: Any, name: str | None, _ctx: ssl.SSLContext) -> None:
                with seen.lock:
                    seen.sni_by_socket[id(sock)] = name

            tls.sni_callback = on_sni
            httpd.socket = tls.wrap_socket(httpd.socket, server_side=True)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        servers.append(httpd)
        return Server(port=int(httpd.server_address[1]), seen=seen)

    yield make
    for httpd in servers:
        httpd.shutdown()
        httpd.server_close()


@pytest.fixture
def connect_map(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """Redirect httpcore's TCP connect from fake public IPs to loopback ports.

    Returns ``(table, connects)``: fill ``table[ip] = port``; ``connects``
    lists every IP a connection was opened for. An unmapped IP fails loudly.
    """
    table: dict[str, int] = {}
    connects: list[str] = []
    original = httpcore._backends.sync.SyncBackend.connect_tcp

    def connect_tcp(
        self: Any,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Any = None,
    ) -> Any:
        connects.append(host)
        if host not in table:
            raise AssertionError(f"connection attempted to unmapped IP {host}")
        return original(
            self, "127.0.0.1", table[host], timeout, local_address, socket_options
        )

    monkeypatch.setattr(httpcore._backends.sync.SyncBackend, "connect_tcp", connect_tcp)
    client_mod._POOL.clear()
    yield table, connects
    client_mod._POOL.clear()


def test_same_host_reuses_one_connection(make_server: Any, connect_map: Any) -> None:
    table, connects = connect_map
    server = make_server()
    table[IP_A] = server.port
    resolver = FakeResolver({"docs.test": [IP_A]})

    for path in ("/a", "/b", "/c"):
        result = client_mod.fetch(f"http://docs.test{path}", resolver=resolver)
        assert result.status == 200

    assert server.seen.connections == 1
    assert connects == [IP_A]
    assert [r["host"] for r in server.seen.requests] == ["docs.test"] * 3
    # The guard ran for every request (no validation is skipped on reuse).
    assert resolver.calls == ["docs.test"] * 3


def test_hostnames_on_one_ip_never_share_a_tls_connection(
    make_server: Any, connect_map: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two names behind one IP: each request travels on a connection whose TLS
    handshake (SNI, certificate check) was for ITS hostname."""
    table, connects = connect_map
    ca = trustme.CA()
    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ca.issue_cert("one.test", "two.test").configure_cert(server_ctx)
    server = make_server(tls=server_ctx)
    table[IP_A] = server.port

    client_ctx = ssl.create_default_context()
    ca.configure_trust(client_ctx)
    monkeypatch.setattr(client_mod, "_ssl_context", lambda: client_ctx)
    resolver = FakeResolver({"one.test": [IP_A], "two.test": [IP_A]})

    for host in ("one.test", "two.test", "one.test", "two.test"):
        assert client_mod.fetch(f"https://{host}/", resolver=resolver).status == 200

    assert server.seen.connections == 2
    assert connects == [IP_A, IP_A]
    for request in server.seen.requests:
        assert request["sni"] == request["host"]
    assert sorted(client_mod._POOL.hostnames()) == ["one.test", "two.test"]


def test_rebinding_to_private_ip_is_blocked_despite_warm_connection(
    make_server: Any, connect_map: Any
) -> None:
    """The name first resolves to a public IP (connection pooled), then to a
    private one: the guard refuses before anything is sent, warm pool or not."""
    table, connects = connect_map
    server = make_server()
    table[IP_A] = server.port
    resolver = SequenceResolver([[IP_A], [PRIVATE_IP]])

    assert client_mod.fetch("http://rebind.test/", resolver=resolver).status == 200
    with pytest.raises(BlockedDestination):
        client_mod.fetch("http://rebind.test/secret", resolver=resolver)

    assert [r["path"] for r in server.seen.requests] == ["/"]
    assert connects == [IP_A]


def test_rebinding_to_another_ip_uses_the_newly_validated_ip(
    make_server: Any, connect_map: Any
) -> None:
    """The name moves from IP A to IP B: the second request opens a connection
    to B (the IP validated for it) instead of reusing the idle one to A."""
    table, connects = connect_map
    server_a, server_b = make_server(), make_server()
    table[IP_A], table[IP_B] = server_a.port, server_b.port
    resolver = SequenceResolver([[IP_A], [IP_B]])

    client_mod.fetch("http://moving.test/first", resolver=resolver)
    client_mod.fetch("http://moving.test/second", resolver=resolver)

    assert [r["path"] for r in server_a.seen.requests] == ["/first"]
    assert [r["path"] for r in server_b.seen.requests] == ["/second"]
    assert connects == [IP_A, IP_B]


def test_redirect_hops_are_validated_with_pooled_clients(
    make_server: Any, connect_map: Any
) -> None:
    """A warm client for the entry host does not exempt a redirect hop: the hop
    to a private address is refused and never connected."""
    table, connects = connect_map
    server = make_server({"/go": (302, {"Location": f"http://{PRIVATE_IP}/meta"})})
    table[IP_A] = server.port
    resolver = FakeResolver({"docs.test": [IP_A]})

    client_mod.fetch("http://docs.test/warm", resolver=resolver)
    with pytest.raises(BlockedDestination):
        client_mod.fetch("http://docs.test/go", resolver=resolver)
    assert connects == [IP_A]


def test_redirect_to_another_host_uses_that_hosts_client(
    make_server: Any, connect_map: Any
) -> None:
    table, _connects = connect_map
    target = make_server()
    entry = make_server({"/go": (301, {"Location": "http://other.test/landed"})})
    table[IP_A], table[IP_B] = entry.port, target.port
    resolver = FakeResolver({"docs.test": [IP_A], "other.test": [IP_B]})

    result = client_mod.fetch("http://docs.test/go", resolver=resolver)

    assert result.final_url == "http://other.test/landed"
    assert [r["host"] for r in target.seen.requests] == ["other.test"]
    assert sorted(client_mod._POOL.hostnames()) == ["docs.test", "other.test"]


def test_cookies_follow_redirect_hops_but_never_the_next_call(
    make_server: Any, connect_map: Any
) -> None:
    table, _connects = connect_map
    server = make_server(
        {"/set": (302, {"Set-Cookie": "sid=abc; Path=/", "Location": "/after"})}
    )
    table[IP_A] = server.port
    resolver = FakeResolver({"docs.test": [IP_A]})

    client_mod.fetch("http://docs.test/set", resolver=resolver)
    client_mod.fetch("http://docs.test/later", resolver=resolver)

    cookies = {r["path"]: r["cookie"] for r in server.seen.requests}
    assert cookies["/after"] == "sid=abc"  # same call: the hop carries it
    assert cookies["/later"] is None  # next call: no state carried over


def test_concurrent_fetches_on_one_host(make_server: Any, connect_map: Any) -> None:
    table, _connects = connect_map
    server = make_server()
    table[IP_A] = server.port
    resolver = FakeResolver({"docs.test": [IP_A]})
    statuses: list[int] = []
    lock = threading.Lock()

    def worker(i: int) -> None:
        status = client_mod.fetch(f"http://docs.test/p{i}", resolver=resolver).status
        with lock:
            statuses.append(status)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert statuses == [200] * 8
    assert len(server.seen.requests) == 8


def test_pool_is_bounded_and_never_closes_a_leased_client() -> None:
    pool = client_mod._HostClientPool(max_hosts=2)
    with pool.lease("a.test") as a:
        with pool.lease("b.test"), pool.lease("c.test"):
            pass  # c evicts a (least recently used) while a is still leased
        assert pool.hostnames() == ["b.test", "c.test"]
        assert not a.is_closed
    assert a.is_closed  # closed once its last lease ended
    pool.clear()


def test_tls_context_matches_verify_true() -> None:
    """The shared context verifies certificates and hostnames like
    ``verify=True`` did (no weakening of TLS for the sake of reuse)."""
    ctx = client_mod._ssl_context()
    assert ctx is client_mod._ssl_context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True
    assert ctx.cert_store_stats()["x509_ca"] > 0
