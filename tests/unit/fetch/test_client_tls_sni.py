"""Real-TLS proof of the connect-on-validated-IP mechanism (SPEC §3.1, M1).

The SSRF defense connects to a *literal validated IP* while presenting the
original hostname as the TLS SNI (and verifying the certificate against that
hostname, not the IP). The MockTransport-based tests in ``test_client.py`` prove
the request carries ``sni_hostname`` + the restored ``Host`` header, but a mock
never opens a socket, so it cannot prove the TLS layer actually honors that SNI.
The M1 security review flagged exactly this as "likely, not confirmed".

This test closes that gap with a real loopback TLS server:

  * ``trustme`` mints a CA and a leaf cert for the hostname ``HOSTNAME`` only
    (the server has NO cert for its IP);
  * the server runs on ``127.0.0.1`` and records the SNI ``server_name`` seen
    during each handshake;
  * the client connects to the URL with the literal IP in the authority, sets
    the ``sni_hostname`` extension and ``Host`` header to ``HOSTNAME``, and
    verifies against the fake CA — the exact mechanism ``app.fetch.client.fetch``
    uses.

If SNI were not sent (or the cert were checked against the IP) the handshake
would fail. A 200 plus the server observing ``server_name == HOSTNAME`` proves
the mechanism end to end over real TLS. No network leaves loopback.
"""

from __future__ import annotations

import http.server
import ssl
import threading
from collections.abc import Iterator

import httpx
import pytest
import trustme

HOSTNAME = "nina-tls-test.example"


class _SNICapture:
    """Shared slot for the SNI server_name seen by the TLS context."""

    def __init__(self) -> None:
        self.seen: list[str | None] = []


def _make_ssl_context(ca: trustme.CA, capture: _SNICapture) -> ssl.SSLContext:
    """Server-side TLS context for ``HOSTNAME`` that records the SNI value."""
    server_cert = ca.issue_cert(HOSTNAME)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_cert.configure_cert(ctx)

    def _on_sni(
        sslobj: ssl.SSLObject, server_name: str | None, context: ssl.SSLContext
    ) -> None:
        capture.seen.append(server_name)

    ctx.sni_callback = _on_sni
    return ctx


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 (stdlib naming)
        body = b"secure-ok"
        self.send_response(200)
        self.send_header("content-type", "text/plain")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        pass  # Silence the test server.


@pytest.fixture
def tls_server() -> Iterator[tuple[str, int, trustme.CA, _SNICapture]]:
    """A loopback HTTPS server with a cert for HOSTNAME; yields host/port/CA/SNI."""
    ca = trustme.CA()
    capture = _SNICapture()
    httpd = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    httpd.socket = _make_ssl_context(ca, capture).wrap_socket(
        httpd.socket, server_side=True
    )
    host, port = httpd.server_address[0], httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield str(host), int(port), ca, capture
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


@pytest.mark.allow_network
def test_tls_sni_presented_when_connecting_on_ip(
    tls_server: tuple[str, int, trustme.CA, _SNICapture],
) -> None:
    """The validated IP is dialed, yet TLS verifies against the hostname via SNI.

    This mirrors app.fetch.client.fetch's mechanism (IP in the URL authority +
    sni_hostname extension + Host override + verify against the trusted CA).
    """
    ip, port, ca, capture = tls_server

    verify_ctx = ssl.create_default_context()
    ca.configure_trust(verify_ctx)

    with httpx.Client(verify=verify_ctx) as client:
        # Dial the literal IP (never the hostname), exactly as the guard would
        # hand it off, but present HOSTNAME as the TLS SNI and the Host header.
        request = client.build_request(
            "GET",
            f"https://{ip}:{port}/",
            headers={"Host": HOSTNAME},
            extensions={"sni_hostname": HOSTNAME},
        )
        response = client.send(request)

    assert response.status_code == 200
    assert response.text == "secure-ok"
    # The server saw the hostname as the TLS SNI, not the IP it was dialed on.
    assert capture.seen == [HOSTNAME]


@pytest.mark.allow_network
def test_tls_without_sni_fails_cert_check(
    tls_server: tuple[str, int, trustme.CA, _SNICapture],
) -> None:
    """Control: dialing the IP without presenting the hostname fails TLS.

    Without SNI/hostname the cert (issued for HOSTNAME only) cannot validate
    against the IP, so the handshake is rejected. This proves the passing test
    above succeeds *because* of the SNI mechanism, not by accident.
    """
    ip, port, ca, _capture = tls_server

    verify_ctx = ssl.create_default_context()
    ca.configure_trust(verify_ctx)

    with httpx.Client(verify=verify_ctx) as client:
        with pytest.raises(httpx.ConnectError):
            client.get(f"https://{ip}:{port}/")
