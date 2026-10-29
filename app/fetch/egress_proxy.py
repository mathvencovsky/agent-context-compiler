"""Guard-enforcing local egress proxy for the browser path (SPEC §3.4).

Chromium is launched with this proxy as its ONLY way out (``--proxy-server``,
loopback not bypassed, and a host-resolver rule that makes every hostname
unresolvable for Chromium itself). Every TCP connection Chromium opens for HTTP,
HTTPS or WebSocket traffic — navigations, every redirect hop, sub-resources,
popups, iframes, workers, service workers, prefetch/preconnect, beacons — is
therefore a request to this proxy, and the proxy:

1. validates the destination with :func:`app.fetch.guard.validate_url` (the
   single destination authority — no IP/port policy is duplicated here);
2. connects ONLY to the guard-validated IP and port (``GuardResult``), never to
   a name resolved a second time. This is the same connect-on-IP seam as the
   httpx client, so it also closes the browser DNS-rebinding window that
   DECISIONS.md 2026-10-11 recorded as residual debt;
3. refuses (403, no detail) anything the guard rejects, and anything it cannot
   parse (400). It fails closed on every unexpected error.

Two request forms reach it:

* ``CONNECT host:port`` (HTTPS, WSS, and WS through a proxy): validated, then
  a blind TCP tunnel to the validated IP. TLS stays end to end between Chromium
  and the origin (Chromium checks the certificate for the original hostname).
* absolute-form ``GET http://host/path HTTP/1.1`` (plain HTTP): validated, the
  request line is rewritten to origin-form, hop-by-hop proxy headers are
  dropped and ``Connection: close`` is forced on both the request and the
  response, so one proxy connection carries exactly one destination.

Chromium's own networking stack (HTTP/2, caching, cookies, CORS, redirects) is
untouched: the proxy only decides WHERE a connection may go. That keeps the
rendered DOM of a normal page identical to the pre-proxy behaviour.

``socket`` is used here, inside ``app/fetch`` and behind the guard, which keeps
the CLAUDE.md rule that outbound clients live only behind the fetch guard.
"""

from __future__ import annotations

import selectors
import socket
import threading
from collections.abc import Callable
from urllib.parse import urlsplit

from app.config import Settings
from app.fetch.guard import GuardResult, Resolver, validate_url

#: Opens the upstream TCP connection for a guard-validated destination. The
#: default connects to ``guard.validated_ip:guard.port``; tests inject one that
#: maps the validated hostname to a loopback fixture (the guard still decides).
Connector = Callable[[GuardResult, float], socket.socket]

#: Header on every refusal the proxy answers itself, so the renderer can tell
#: "the proxy refused this navigation" from an origin's own error page.
REFUSAL_HEADER = b"X-Nina-Egress"

_MAX_HEAD_BYTES = 64 * 1024
_IDLE_TIMEOUT_S = 60.0
_CHUNK = 64 * 1024
_HOP_BY_HOP = frozenset(
    {
        b"connection",
        b"keep-alive",
        b"proxy-connection",
        b"proxy-authorization",
        b"proxy-authenticate",
    }
)


def default_connector(guard: GuardResult, timeout: float) -> socket.socket:
    """Connect to the guard-validated literal IP (no second DNS resolution)."""
    return socket.create_connection((guard.validated_ip, guard.port), timeout=timeout)


class _Refused(Exception):
    """Internal: the request is answered with ``status`` and closed."""

    def __init__(self, status: bytes) -> None:
        super().__init__(status.decode())
        self.status = status


def _read_head(sock: socket.socket) -> tuple[bytes, bytes]:
    """Read up to the end of an HTTP head; return ``(head, leftover)``."""
    data = b""
    while b"\r\n\r\n" not in data:
        if len(data) > _MAX_HEAD_BYTES:
            raise _Refused(b"431 Request Header Fields Too Large")
        chunk = sock.recv(_CHUNK)
        if not chunk:
            raise ConnectionError("peer closed before the end of the head")
        data += chunk
    head, _, leftover = data.partition(b"\r\n\r\n")
    return head, leftover


def _force_close(lines: list[bytes]) -> bytes:
    """Drop hop-by-hop headers and force ``Connection: close`` on a head."""
    kept = [lines[0]]
    for line in lines[1:]:
        name = line.split(b":", 1)[0].strip().lower()
        if name in _HOP_BY_HOP:
            continue
        kept.append(line)
    kept.append(b"Connection: close")
    return b"\r\n".join(kept) + b"\r\n\r\n"


class EgressProxy:
    """A loopback HTTP proxy that lets Chromium reach only guard-approved IPs.

    Use as a context manager; ``url`` is the ``http://127.0.0.1:<port>`` value
    for Playwright's ``proxy={"server": ...}``. ``refused`` records every
    destination the guard rejected (for diagnostics and tests).
    """

    def __init__(
        self,
        *,
        resolver: Resolver | None,
        settings: Settings,
        connector: Connector | None = None,
    ) -> None:
        self._resolver = resolver
        self._settings = settings
        self._connector = connector or default_connector
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(128)
        self._lock = threading.Lock()
        self._open: set[socket.socket] = set()
        self._closed = False
        self.refused: list[str] = []
        port = self._listener.getsockname()[1]
        self.url = f"http://127.0.0.1:{port}"
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)

    def __enter__(self) -> EgressProxy:
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        """Stop accepting and tear down every open connection."""
        with self._lock:
            self._closed = True
            sockets = list(self._open)
            self._open.clear()
        try:
            self._listener.close()
        except OSError:
            pass
        for sock in sockets:
            _close_quietly(sock)

    # ------------------------------------------------------------------ #
    def _track(self, sock: socket.socket) -> bool:
        with self._lock:
            if self._closed:
                return False
            self._open.add(sock)
            return True

    def _untrack(self, sock: socket.socket) -> None:
        with self._lock:
            self._open.discard(sock)
        _close_quietly(sock)

    def _accept_loop(self) -> None:
        while True:
            try:
                client, _addr = self._listener.accept()
            except OSError:
                return  # Listener closed.
            if not self._track(client):
                _close_quietly(client)
                return
            threading.Thread(target=self._handle, args=(client,), daemon=True).start()

    def _validate(self, probe_url: str) -> GuardResult:
        try:
            return validate_url(
                probe_url, resolver=self._resolver, settings=self._settings
            )
        except Exception as exc:  # noqa: BLE001 — fail closed on ANY error.
            self.refused.append(probe_url)
            raise _Refused(b"403 Forbidden") from exc

    def _connect(self, guard: GuardResult) -> socket.socket:
        try:
            upstream = self._connector(guard, float(self._settings.connect_timeout))
        except OSError as exc:
            raise _Refused(b"502 Bad Gateway") from exc
        if not self._track(upstream):
            _close_quietly(upstream)
            raise _Refused(b"502 Bad Gateway")
        upstream.settimeout(_IDLE_TIMEOUT_S)
        return upstream

    def _handle(self, client: socket.socket) -> None:
        upstream: socket.socket | None = None
        try:
            client.settimeout(_IDLE_TIMEOUT_S)
            head, leftover = _read_head(client)
            lines = head.split(b"\r\n")
            parts = lines[0].split(b" ")
            if len(parts) != 3:
                raise _Refused(b"400 Bad Request")
            method, target, version = parts
            if method.upper() == b"CONNECT":
                authority = target.decode("ascii", errors="strict")
                if "/" in authority or not authority:
                    raise _Refused(b"400 Bad Request")
                # The guard parses the authority; we connect ONLY to what it
                # validated, so no parser differential can redirect the tunnel.
                guard = self._validate(f"https://{authority}/")
                upstream = self._connect(guard)
                client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                if leftover:
                    upstream.sendall(leftover)
                self._pipe(client, upstream, rewrite_response=False)
                return
            url = target.decode("ascii", errors="strict")
            if not url.lower().startswith("http://"):
                # Origin-form or https absolute-form: not a proxy request we
                # serve (https always arrives as CONNECT).
                raise _Refused(b"400 Bad Request")
            guard = self._validate(url)
            split = urlsplit(guard.normalized_url)
            path = (split.path or "/") + (f"?{split.query}" if split.query else "")
            lines[0] = b" ".join((method, path.encode("ascii"), version))
            upstream = self._connect(guard)
            upstream.sendall(_force_close(lines) + leftover)
            self._pipe(client, upstream, rewrite_response=True)
        except _Refused as refused:
            try:
                client.sendall(
                    b"HTTP/1.1 "
                    + refused.status
                    + b"\r\n"
                    + REFUSAL_HEADER
                    + b": refused\r\n"
                    b"Content-Length: 0\r\nConnection: close\r\n\r\n"
                )
            except OSError:
                pass
        except Exception:  # noqa: BLE001 — any failure just drops the connection.
            pass
        finally:
            if upstream is not None:
                self._untrack(upstream)
            self._untrack(client)

    def _pipe(
        self, client: socket.socket, upstream: socket.socket, *, rewrite_response: bool
    ) -> None:
        """Relay bytes both ways until either side closes or idles out."""
        pending_head = b"" if rewrite_response else None
        with selectors.DefaultSelector() as sel:
            sel.register(client, selectors.EVENT_READ, upstream)
            sel.register(upstream, selectors.EVENT_READ, client)
            while True:
                events = sel.select(timeout=_IDLE_TIMEOUT_S)
                if not events:
                    return
                for key, _mask in events:
                    src: socket.socket = key.fileobj  # type: ignore[assignment]
                    dst: socket.socket = key.data
                    data = src.recv(_CHUNK)
                    if not data:
                        return
                    if src is upstream and pending_head is not None:
                        pending_head += data
                        if b"\r\n\r\n" not in pending_head:
                            if len(pending_head) > _MAX_HEAD_BYTES:
                                return
                            continue
                        head, _, rest = pending_head.partition(b"\r\n\r\n")
                        data = _force_close(head.split(b"\r\n")) + rest
                        pending_head = None
                    dst.sendall(data)


def _close_quietly(sock: socket.socket) -> None:
    try:
        sock.close()
    except OSError:
        pass
