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
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from app.config import Settings, get_settings
from app.fetch import detect
from app.fetch.errors import FetchFailed, Timeout, TooLarge
from app.fetch.guard import GuardResult, Resolver, SocketResolver, validate_url

_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})

#: Public alias for the injectable transport seam. Exported so callers (e.g. the
#: M5 API layer) can type a ``transport`` dependency WITHOUT importing ``httpx``
#: themselves — ``httpx`` stays confined to ``app/fetch`` (CLAUDE.md).
Transport = httpx.BaseTransport


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


def _read_capped_body(response: httpx.Response, limit: int) -> bytes:
    """Stream the body, aborting with ``TooLarge`` once ``limit`` is crossed.

    The whole body is never buffered: iteration stops on the first chunk that
    pushes the running total past ``limit``, so at most ``limit`` + one chunk is
    ever held. A ``Content-Length`` that already exceeds the limit short-circuits
    before any body is read, but it is never trusted as the sole check.
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

    buffer = bytearray()
    for chunk in response.iter_bytes():
        buffer.extend(chunk)
        if len(buffer) > limit:
            response.close()
            raise TooLarge(
                f"response body exceeded limit of {limit} bytes",
                details={"limit": limit, "bytes_read": len(buffer)},
            )
    return bytes(buffer)


def fetch(
    url: str,
    *,
    resolver: Resolver | None = None,
    transport: httpx.BaseTransport | None = None,
    settings: Settings | None = None,
    method: str = "GET",
) -> FetchResult:
    """Fetch ``url`` safely and return a :class:`FetchResult`.

    Redirects are followed manually (max ``settings.max_redirects`` hops); each
    ``Location`` is re-validated by the guard, so a redirect to a private IP
    raises ``BlockedDestination``. Body size, connect and total timeouts are
    enforced per SPEC §3.1.
    """
    settings = settings or get_settings()
    resolver = resolver or SocketResolver()

    timeout = httpx.Timeout(settings.total_timeout, connect=settings.connect_timeout)
    user_agent = settings.user_agent()

    started = time.monotonic()
    current_url = url
    redirects = 0

    client = httpx.Client(
        follow_redirects=False,
        verify=True,
        timeout=timeout,
        trust_env=False,
        transport=transport,
    )
    try:
        while True:
            guard = validate_url(current_url, resolver=resolver, settings=settings)
            ip_url = _build_ip_url(guard, current_url)

            request = client.build_request(
                method,
                ip_url,
                headers={
                    "Host": _host_header(guard, current_url),
                    "User-Agent": user_agent,
                },
                extensions={"sni_hostname": guard.hostname},
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
                body = _read_capped_body(response, settings.max_body_bytes)
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
        client.close()
