"""Typed synchronous client for the NINA API — the ``nina-client`` SDK (M9).

:class:`NinaClient` is the public surface a pilot imports. It owns its OWN
``httpx.Client`` (NOT the server's fetch guard — the guard governs untrusted
page content; this SDK calls NINA's OWN API, a trusted endpoint, mirroring the
server's Jina/OpenAI provider clients; see the repo DECISIONS.md). Every request
carries ``Authorization: Bearer <api_key>``; the key is only ever placed on that
header and is never logged by the SDK.

Transient failures (HTTP 429 and 5xx) are retried with exponential backoff up to
``max_retries``, honoring the ``Retry-After`` response header when the server
sends one. When retries are exhausted (or the error is non-transient), the SPEC
§8 error envelope is decoded into the matching typed error from
:mod:`nina_client.errors` (e.g. :class:`UnauthorizedError`,
:class:`RateLimitedError`). The ``transport`` is injectable so tests drive the
client with an ``httpx.MockTransport`` and never touch the network.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import httpx
from nina_client.errors import NinaError, error_from_envelope
from nina_client.models import AssembleResponse, ResolveResponse

__all__ = ["NinaClient"]

#: HTTP statuses worth retrying with backoff (rate limit + transient server).
_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})

#: Base delay (seconds) for the exponential backoff when no Retry-After is sent.
_BASE_BACKOFF_SECONDS = 0.5

#: Resolve formats the API understands (SPEC §8).
ResolveFormats = list[str]


class NinaClient:
    """Typed client for the NINA context-compiler API.

    Parameters
    ----------
    base_url:
        Base URL of the running NINA API (e.g. ``https://nina.example.com``).
    api_key:
        A ``nina_...`` key minted with ``python -m app.admin create-key``. Sent
        as ``Authorization: Bearer <api_key>``; never logged.
    timeout:
        Per-request timeout in seconds.
    max_retries:
        How many times a 429/5xx is retried with exponential backoff before the
        typed error is raised. ``0`` disables retrying.
    transport:
        Optional ``httpx`` transport (tests pass an ``httpx.MockTransport``).
    sleep:
        Injectable sleep function (tests pass a no-op so backoff adds no delay).
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout: float = 30.0,
        max_retries: int = 3,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._max_retries = max_retries
        self._sleep = sleep
        # The key lives only on the Authorization header; it is never logged or
        # echoed by any method on this client.
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            transport=transport,
            timeout=timeout,
        )

    def __enter__(self) -> NinaClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        self._client.close()

    # ----------------------------------------------------------------- API --
    def resolve(
        self,
        url: str,
        *,
        formats: ResolveFormats | None = None,
        force_refresh: bool = False,
        render: str = "auto",
    ) -> ResolveResponse:
        """Resolve a URL to a document (``POST /v1/documents/resolve``)."""
        payload: dict[str, Any] = {
            "url": url,
            "formats": formats if formats is not None else ["markdown", "blocks"],
            "force_refresh": force_refresh,
            "render": render,
        }
        body = self._request("POST", "/v1/documents/resolve", json=payload)
        return ResolveResponse.from_dict(body)

    def assemble(
        self,
        query: str,
        urls: list[str],
        *,
        max_tokens: int = 4000,
        include_evidence: bool = True,
    ) -> AssembleResponse:
        """Assemble a token-budgeted context package (``POST /v1/context/assemble``)."""
        payload: dict[str, Any] = {
            "query": query,
            "urls": urls,
            "max_tokens": max_tokens,
            "include_evidence": include_evidence,
        }
        body = self._request("POST", "/v1/context/assemble", json=payload)
        return AssembleResponse.from_dict(body)

    def get_document(
        self,
        document_id: str,
        *,
        version_id: str | None = None,
    ) -> ResolveResponse:
        """Fetch a stored document by id (``GET /v1/documents/{document_id}``)."""
        params: dict[str, str] = {}
        if version_id is not None:
            params["version_id"] = version_id
        body = self._request(
            "GET", f"/v1/documents/{document_id}", params=params or None
        )
        return ResolveResponse.from_dict(body)

    # ------------------------------------------------------------- internal --
    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Send a request with retry/backoff and return the decoded JSON body.

        Retries 429/5xx up to ``max_retries`` honoring ``Retry-After``; on a
        non-2xx that is not retried (or after retries are exhausted) the SPEC §8
        envelope is raised as the matching typed :class:`NinaError`.
        """
        last_response: httpx.Response | None = None
        for attempt in range(self._max_retries + 1):
            response = self._client.request(method, path, json=json, params=params)
            last_response = response
            if response.status_code not in _RETRY_STATUS:
                break
            if attempt == self._max_retries:
                break
            self._sleep(self._backoff_seconds(response, attempt))

        assert last_response is not None  # the loop always runs at least once
        return self._handle(last_response)

    def _handle(self, response: httpx.Response) -> dict[str, Any]:
        """Return the decoded body on 2xx, else raise the typed §8 error."""
        if response.is_success:
            return _as_json_object(response)
        raise error_from_envelope(
            _safe_json(response),
            status_code=response.status_code,
            retry_after=_header_retry_after(response),
        )

    def _backoff_seconds(self, response: httpx.Response, attempt: int) -> float:
        """Delay before the next retry: Retry-After if present, else backoff."""
        header = _header_retry_after(response)
        if header is not None:
            return header
        return _BASE_BACKOFF_SECONDS * float(2**attempt)


def _header_retry_after(response: httpx.Response) -> float | None:
    """Parse the ``Retry-After`` header as integer seconds, else ``None``."""
    value = response.headers.get("Retry-After")
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _safe_json(response: httpx.Response) -> Any:
    """Decode a response body, tolerating a non-JSON error page."""
    try:
        return response.json()
    except ValueError:
        return None


def _as_json_object(response: httpx.Response) -> dict[str, Any]:
    """Decode a 2xx body expected to be a JSON object."""
    body: Any = _safe_json(response)
    if not isinstance(body, dict):
        raise NinaError(
            "expected a JSON object from the NINA API",
            status_code=response.status_code,
        )
    return body
