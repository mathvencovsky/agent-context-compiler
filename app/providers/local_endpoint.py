"""The one exception to "no loopback": a configured local model server.

The fetch guard (``app/fetch/guard.py``) blocks loopback and private addresses
because fetched URLs come from users. Model providers are different: they talk
to fixed, trusted endpoints with their own ``httpx`` client and never through
the guard (DECISIONS 2026-10-13). A local embeddings or rerank server (llama.cpp
server, TEI) on the operator's machine is such an endpoint, but it lives on
loopback, so the exception is kept as narrow as possible (DECISIONS 2026-10-29):

* the base URL comes only from configuration (``EMBEDDINGS_BASE_URL``,
  ``RERANKER_BASE_URL``), never from a request, so no user URL can reach it;
* it must be ``http``/``https`` to a loopback IP literal (``127.0.0.0/8`` or
  ``::1``) with no credentials, query or fragment — no host name, so no DNS
  answer can redirect it;
* :func:`local_client` never follows redirects and ignores proxy environment
  variables, so a response cannot send the call elsewhere.
"""

from __future__ import annotations

import ipaddress
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

if TYPE_CHECKING:
    import httpx

#: Timeout for one local-model call; CPU reranking of 30 long pairs is slow.
LOCAL_TIMEOUT_SECONDS = 600.0


class LocalEndpointError(ValueError):
    """A local-provider base URL that is not a loopback IP literal."""


def validate_local_base_url(url: str) -> str:
    """Return ``url`` without a trailing slash, or raise :class:`LocalEndpointError`."""
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https"):
        raise LocalEndpointError(f"local provider URL must be http(s): {url!r}")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise LocalEndpointError(
            "local provider URL must not carry credentials, a query or a fragment"
        )
    host = parts.hostname or ""
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise LocalEndpointError(
            f"local provider URL must use a loopback IP literal "
            f"(127.0.0.1 or [::1]), not {host!r}"
        ) from exc
    if not address.is_loopback:
        raise LocalEndpointError(f"local provider address is not loopback: {host}")
    try:
        parts.port  # noqa: B018 — raises on an invalid port
    except ValueError as exc:
        raise LocalEndpointError(f"invalid port in {url!r}") from exc
    return url.strip().rstrip("/")


def local_client(transport: httpx.BaseTransport | None = None) -> httpx.Client:
    """An ``httpx`` client for a validated local endpoint (no redirects/proxies)."""
    import httpx

    return httpx.Client(
        timeout=LOCAL_TIMEOUT_SECONDS,
        follow_redirects=False,
        trust_env=False,
        transport=transport,
    )
