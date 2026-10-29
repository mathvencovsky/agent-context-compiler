"""Secure HTTP client that connects on the validated IP (SPEC §3.1, M1).

This is the ONLY module outside tests that imports ``httpx`` for outbound
requests (CLAUDE.md non-negotiable). Every request — including each redirect
hop and the robots.txt fetch routed through here — is validated by
``app.fetch.guard`` first.

Closing DNS rebinding (decision D1): the guard resolves and validates the
hostname, and this client sends the request to the URL with the literal
validated IP substituted for the host. To keep virtual hosting and TLS correct
we then:

  * override the ``Host`` header back to the original hostname (httpx otherwise
    derives it from the IP in the URL authority);
  * set the ``sni_hostname`` request extension to the original hostname so
    httpcore uses it as the TLS ``server_hostname`` (verified on
    httpx 0.28.1 / httpcore 1.0.9);
  * keep ``verify=True``.

No second DNS resolution occurs: httpcore connects to the host in the request
URL, which is the already-validated IP.

Reused clients (M10b efficiency, DECISIONS.md 2026-10-29): building an
``httpx.Client`` with ``verify=True`` costs ~30-50 ms (the TLS context), and a
client per fetch never reuses a connection. Now one ``ssl.SSLContext`` (same
settings as ``verify=True``: certifi CAs, hostname check) is built per process,
and real-network fetches lease a keep-alive client from a pool of ONE CLIENT
PER HOSTNAME. The guard still validates every request and every redirect hop
before anything is sent, and the request URL still carries the validated IP,
so a pooled connection is only ever reused for a request whose IP the guard
has just validated for that same hostname (httpcore keys connections by
scheme, IP and port). Clients are never shared across hostnames: two names on
one CDN IP would otherwise reuse a TLS connection authenticated (SNI and
certificate) for the other name. Cookies never outlive one ``fetch`` call
(they still follow its redirect hops, as before), and idle connections are
dropped after a few seconds, below common server keep-alive timeouts.
"""

from __future__ import annotations

import functools
import ssl
import threading
import time
import zlib
from collections import OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from http.cookiejar import CookieJar, DefaultCookiePolicy
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from app.config import Settings, get_settings
from app.fetch import detect
from app.fetch.errors import FetchFailed, Timeout, TooLarge
from app.fetch.guard import GuardResult, Resolver, SocketResolver, validate_url

_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
#: Content-Encodings decoded (the ones httpx 0.28 decodes without extras).
_DECODED_ENCODINGS = frozenset({"gzip", "deflate"})

#: Public alias for the injectable transport seam. Exported so callers (e.g. the
#: M5 API layer) can type a ``transport`` dependency WITHOUT importing ``httpx``
#: themselves — ``httpx`` stays confined to ``app/fetch`` (CLAUDE.md).
Transport = httpx.BaseTransport


#: Idle keep-alive connections are dropped after this many seconds: below the
#: 5 s default of Apache's KeepAliveTimeout, so a pooled connection is not
#: reused just as a common server closes it.
_KEEPALIVE_EXPIRY_S = 4.0
#: Idle connections kept per hostname client.
_MAX_KEEPALIVE_PER_HOST = 4
#: Hostname clients kept by the pool (least recently used evicted first).
_MAX_POOLED_HOSTS = 64


@functools.cache
def _ssl_context() -> ssl.SSLContext:
    """The process-wide TLS context: exactly what ``verify=True`` builds.

    httpx's ``verify=True`` with ``trust_env=False`` is
    ``ssl.create_default_context(cafile=certifi.where())``; building it once
    keeps certificate and hostname verification identical while saving the
    ~30-50 ms each new client paid.
    """
    return httpx.create_ssl_context(verify=True, trust_env=False)


def _no_cookie_jar() -> CookieJar:
    """A cookie jar that refuses every cookie (pooled clients keep no state).

    Given to the ``cookies`` setter as a bare ``CookieJar``: httpx keeps that
    jar (and its policy) as is, whereas a ``Cookies`` would be copied into a
    new jar with the default, accept-all policy.
    """
    return CookieJar(policy=DefaultCookiePolicy(allowed_domains=[]))


def _new_client(transport: httpx.BaseTransport | None) -> httpx.Client:
    client = httpx.Client(
        follow_redirects=False,
        verify=_ssl_context(),
        timeout=httpx.Timeout(20.0),
        trust_env=False,
        transport=transport,
        limits=httpx.Limits(
            max_connections=None,
            max_keepalive_connections=_MAX_KEEPALIVE_PER_HOST,
            keepalive_expiry=_KEEPALIVE_EXPIRY_S,
        ),
    )
    client.cookies = _no_cookie_jar()
    return client


@dataclass
class _PoolEntry:
    client: httpx.Client
    leases: int = 0
    evicted: bool = False


class _HostClientPool:
    """Keep-alive ``httpx.Client`` per hostname, bounded and thread-safe.

    A client is leased for one request (or one redirect hop). An evicted client
    is closed once its last lease ends, never under a request in flight.
    """

    def __init__(self, max_hosts: int) -> None:
        self._max_hosts = max_hosts
        self._entries: OrderedDict[str, _PoolEntry] = OrderedDict()
        self._lock = threading.Lock()

    @contextmanager
    def lease(self, hostname: str) -> Iterator[httpx.Client]:
        to_close: list[httpx.Client] = []
        with self._lock:
            entry = self._entries.get(hostname)
            if entry is None:
                entry = _PoolEntry(client=_new_client(None))
                self._entries[hostname] = entry
                while len(self._entries) > self._max_hosts:
                    _host, old = self._entries.popitem(last=False)
                    old.evicted = True
                    if old.leases == 0:
                        to_close.append(old.client)
            self._entries.move_to_end(hostname)
            entry.leases += 1
        for client in to_close:
            client.close()
        try:
            yield entry.client
        finally:
            with self._lock:
                entry.leases -= 1
                close_now = entry.evicted and entry.leases == 0
            if close_now:
                entry.client.close()

    def hostnames(self) -> list[str]:
        """Pooled hostnames, least recently used first (for tests)."""
        with self._lock:
            return list(self._entries)

    def clear(self) -> None:
        """Drop every client, closing the ones not leased right now."""
        with self._lock:
            entries = list(self._entries.values())
            self._entries.clear()
            for entry in entries:
                entry.evicted = True
        for entry in entries:
            if entry.leases == 0:
                entry.client.close()


_POOL = _HostClientPool(_MAX_POOLED_HOSTS)


@dataclass(frozen=True)
class FetchResult:
    """Typed result of a successful fetch (SPEC §3.1 / ROADMAP M1)."""

    final_url: str
    status: int
    headers: httpx.Headers
    body: bytes
    content_type: str
    redirects: int
    elapsed_ms: int


def _build_ip_url(guard: GuardResult, original_url: str) -> str:
    """Return the request URL with the validated IP substituted for the host.

    IPv6 literals are bracketed. Path/query are preserved; the port is carried
    only when the original URL stated one (so defaults stay implicit).
    """
    parts = urlsplit(original_url)
    host = (
        f"[{guard.validated_ip}]" if ":" in guard.validated_ip else guard.validated_ip
    )
    netloc = f"{host}:{parts.port}" if parts.port is not None else host
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, ""))


def _host_header(guard: GuardResult, original_url: str) -> str:
    """Original hostname for the ``Host`` header, with a non-default port only."""
    parts = urlsplit(original_url)
    default = parts.scheme == "http" and guard.port == 80
    default = default or (parts.scheme == "https" and guard.port == 443)
    if parts.port is not None and not default:
        return f"{guard.hostname}:{parts.port}"
    return guard.hostname


class _CappedInflater:
    """Incremental gzip/deflate decoder that never inflates past a budget.

    Mirrors httpx 0.28's ``GZipDecoder``/``DeflateDecoder`` semantics (single
    gzip member; deflate falls back to raw deflate when the first chunk is not
    zlib-wrapped) so a normal page decodes to the same bytes as before, but
    every ``decompress`` call is bounded by ``max_length``: a decompression
    bomb is stopped after ``limit`` + 1 bytes instead of being inflated in full
    (audit A1, 2026-10-28).
    """

    def __init__(self, encoding: str) -> None:
        self._raw_fallback = encoding == "deflate"
        wbits = zlib.MAX_WBITS | 16 if encoding == "gzip" else zlib.MAX_WBITS
        self._inflater = zlib.decompressobj(wbits)
        self._first = True

    def decode(self, data: bytes, room: int) -> bytes:
        """Inflate ``data``, returning at most ``room`` bytes."""
        first, self._first = self._first, False
        out = bytearray()
        while data and len(out) < room:
            try:
                out += self._inflater.decompress(data, room - len(out))
            except zlib.error as exc:
                if first and self._raw_fallback and not out:
                    self._inflater = zlib.decompressobj(-zlib.MAX_WBITS)
                    first = False
                    continue
                raise FetchFailed(
                    f"invalid compressed body: {exc}", details={"error": str(exc)}
                ) from exc
            data = self._inflater.unconsumed_tail
        return bytes(out)

    def flush(self, room: int) -> bytes:
        try:
            return self._inflater.flush(max(room, 1))
        except zlib.error as exc:
            raise FetchFailed(
                f"invalid compressed body: {exc}", details={"error": str(exc)}
            ) from exc


def _content_decoder(response: httpx.Response) -> _CappedInflater | None:
    """Pick the body decoder from ``Content-Encoding`` (httpx semantics).

    ``identity`` and values httpx does not decode are skipped exactly as httpx
    does (the body passes through as-is, which cannot expand). More than one
    real encoding (``gzip, gzip``) is refused: no legitimate server stacks
    them, and each layer multiplies the expansion ratio.
    """
    values = response.headers.get_list("content-encoding", split_commas=True)
    encodings = [
        v.strip().lower() for v in values if v.strip().lower() in _DECODED_ENCODINGS
    ]
    if len(encodings) > 1:
        response.close()
        raise FetchFailed(
            f"stacked Content-Encoding is not supported: {', '.join(encodings)}",
            details={"content_encoding": encodings},
        )
    return _CappedInflater(encodings[0]) if encodings else None


def _too_large(response: httpx.Response, limit: int, read: int) -> TooLarge:
    response.close()
    return TooLarge(
        f"response body exceeded limit of {limit} bytes",
        details={"limit": limit, "bytes_read": read},
    )


def _past_deadline(response: httpx.Response, deadline: float, url: str) -> Timeout:
    response.close()
    return Timeout(
        f"request to {url} exceeded the total timeout",
        details={"url": url},
    )


def _read_capped_body(
    response: httpx.Response,
    limit: int,
    *,
    deadline: float | None = None,
    url: str = "",
) -> bytes:
    """Stream and decode the body, aborting once ``limit`` or ``deadline`` passes.

    The limit applies to the DECODED bytes, enforced incrementally while
    inflating (a compressed body can never expand past ``limit`` + 1 bytes in
    memory). A ``Content-Length`` that already exceeds the limit short-circuits
    before any body is read, but it is never trusted as the sole check. The
    deadline is wall-clock (audit M1): a server that trickles bytes cannot hold
    the request past the total timeout.
    """
    declared = response.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > limit:
                response.close()
                raise TooLarge(
                    f"Content-Length {declared} exceeds limit {limit}",
                    details={"limit": limit, "content_length": int(declared)},
                )
        except ValueError:
            pass  # Malformed header: fall through to streaming enforcement.

    if response.is_stream_consumed:
        # An in-memory response (httpx.Response(content=...), as injected
        # transports build) was already read and decoded by httpx; the network
        # path always streams and never reaches this branch.
        if len(response.content) > limit:
            raise _too_large(response, limit, len(response.content))
        return response.content

    decoder = _content_decoder(response)
    buffer = bytearray()
    try:
        for chunk in response.iter_raw():
            if deadline is not None and time.monotonic() > deadline:
                raise _past_deadline(response, deadline, url)
            room = limit - len(buffer) + 1
            buffer += decoder.decode(chunk, room) if decoder else chunk
            if len(buffer) > limit:
                raise _too_large(response, limit, len(buffer))
        if decoder is not None:
            buffer += decoder.flush(limit - len(buffer) + 1)
            if len(buffer) > limit:
                raise _too_large(response, limit, len(buffer))
    except httpx.TimeoutException as exc:
        response.close()
        raise Timeout(
            f"request to {url} timed out: {exc}", details={"url": url}
        ) from exc
    except httpx.HTTPError as exc:
        response.close()
        raise FetchFailed(
            f"transport error reading {url}: {exc}", details={"url": url}
        ) from exc
    return bytes(buffer)


def fetch(
    url: str,
    *,
    resolver: Resolver | None = None,
    transport: httpx.BaseTransport | None = None,
    settings: Settings | None = None,
    method: str = "GET",
    accept: str | None = None,
) -> FetchResult:
    """Fetch ``url`` safely and return a :class:`FetchResult`.

    Redirects are followed manually (max ``settings.max_redirects`` hops); each
    ``Location`` is re-validated by the guard, so a redirect to a private IP
    raises ``BlockedDestination``. Body size, connect and total timeouts are
    enforced per SPEC §3.1. ``accept`` (None by default: no ``Accept`` header)
    is sent as the ``Accept`` header on every hop (SPEC §3.5 opt-in).
    """
    settings = settings or get_settings()
    resolver = resolver or SocketResolver()
    user_agent = settings.user_agent()

    started = time.monotonic()
    deadline = started + settings.total_timeout
    current_url = url
    redirects = 0
    # Cookies follow this call's redirect hops and nothing else.
    cookies = httpx.Cookies()

    # An injected transport (tests, offline tools) gets a client of its own for
    # this call; the real network goes through the per-hostname pool.
    own_client = _new_client(transport) if transport is not None else None

    @contextmanager
    def client_for(hostname: str) -> Iterator[httpx.Client]:
        if own_client is not None:
            yield own_client
        else:
            with _POOL.lease(hostname) as pooled:
                yield pooled

    try:
        while True:
            guard = validate_url(current_url, resolver=resolver, settings=settings)
            ip_url = _build_ip_url(guard, current_url)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise Timeout(
                    f"request to {current_url} exceeded the total timeout",
                    details={"url": current_url},
                )

            headers = {
                "Host": _host_header(guard, current_url),
                "User-Agent": user_agent,
            }
            if accept is not None:
                headers["Accept"] = accept
            with client_for(guard.hostname) as client:
                request = client.build_request(
                    method,
                    ip_url,
                    headers=headers,
                    cookies=cookies,
                    extensions={"sni_hostname": guard.hostname},
                    # Per-operation timeouts never outlive the total deadline.
                    timeout=httpx.Timeout(
                        min(float(settings.total_timeout), remaining),
                        connect=min(float(settings.connect_timeout), remaining),
                    ),
                )

                try:
                    response = client.send(request, stream=True)
                except (
                    httpx.ConnectTimeout,
                    httpx.ReadTimeout,
                    httpx.WriteTimeout,
                    httpx.PoolTimeout,
                    httpx.TimeoutException,
                ) as exc:
                    raise Timeout(
                        f"request to {current_url} timed out: {exc}",
                        details={"url": current_url},
                    ) from exc
                except httpx.HTTPError as exc:
                    raise FetchFailed(
                        f"transport error fetching {current_url}: {exc}",
                        details={"url": current_url},
                    ) from exc
                cookies.extract_cookies(response)

                location = response.headers.get("location")
                if response.status_code in _REDIRECT_STATUSES and location is not None:
                    response.close()
                    if redirects >= settings.max_redirects:
                        raise FetchFailed(
                            f"too many redirects (>{settings.max_redirects})",
                            details={"url": url, "redirects": redirects},
                        )
                    redirects += 1
                    current_url = urljoin(current_url, location)
                    continue

                # Final response (non-redirect, or redirect without a Location).
                try:
                    body = _read_capped_body(
                        response,
                        settings.max_body_bytes,
                        deadline=deadline,
                        url=current_url,
                    )
                finally:
                    response.close()

            media_type, _charset = detect.parse_content_type(
                response.headers.get("content-type")
            )
            elapsed_ms = int((time.monotonic() - started) * 1000)
            return FetchResult(
                final_url=current_url,
                status=response.status_code,
                headers=response.headers,
                body=body,
                content_type=media_type,
                redirects=redirects,
                elapsed_ms=elapsed_ms,
            )
    finally:
        if own_client is not None:
            own_client.close()
