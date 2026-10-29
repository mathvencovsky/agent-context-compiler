"""Thin typed HTTP client for the NINA API consumed by the MCP server (SPEC §9).

The MCP server is a THIN CLIENT of the public HTTP API: it reads ``API_URL`` and
``API_KEY`` from the environment and calls NINA over its own ``httpx`` client.
Using ``httpx`` directly here is deliberate and allowed: the fetch guard
(``app/fetch/guard.py``) exists for UNTRUSTED page content, but this client talks
to NINA's OWN API — a trusted endpoint, exactly like the Jina/OpenAI provider
clients (recorded in DECISIONS.md). The API_KEY is sent as a Bearer header and is
NEVER logged.

The transport is injectable (``transport=`` kwarg) so tests drive the client with
an ``httpx.MockTransport`` and never touch the network.
"""

from __future__ import annotations

import os
import re
from typing import Any
from urllib.parse import quote

import httpx

__all__ = ["NinaApiClient", "MissingApiConfig"]

#: Environment variables carrying the API location and credential (SPEC §9).
API_URL_ENV = "API_URL"
API_KEY_ENV = "API_KEY"

#: Request timeout in seconds for a single API call.
_TIMEOUT_SECONDS = 60.0


class MissingApiConfig(RuntimeError):
    """Raised when API_URL or API_KEY is absent from the environment.

    The message names only the missing variable, never a secret value.
    """


class NinaApiClient:
    """Typed, synchronous HTTP client for the NINA API.

    Construct from the environment with :meth:`from_env`, or pass ``base_url`` and
    ``api_key`` explicitly. The ``transport`` argument is forwarded to the
    underlying :class:`httpx.Client`, letting tests inject an
    :class:`httpx.MockTransport` so no real network call is made.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        # The key lives only on the Authorization header; it is never logged or
        # echoed back by any method on this client.
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            transport=transport,
            timeout=_TIMEOUT_SECONDS,
        )

    @classmethod
    def from_env(cls, *, transport: httpx.BaseTransport | None = None) -> NinaApiClient:
        """Build a client from ``API_URL``/``API_KEY``, failing loudly if unset."""
        base_url = os.environ.get(API_URL_ENV)
        api_key = os.environ.get(API_KEY_ENV)
        missing = [
            name
            for name, value in ((API_URL_ENV, base_url), (API_KEY_ENV, api_key))
            if not value
        ]
        if missing:
            raise MissingApiConfig(
                f"{' and '.join(missing)} must be set in the environment for the "
                "NINA MCP server to reach the API."
            )
        assert base_url is not None and api_key is not None  # narrowed by `missing`
        return cls(base_url=base_url, api_key=api_key, transport=transport)

    def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        self._client.close()

    def resolve(self, url: str, *, formats: list[str]) -> dict[str, Any]:
        """Call ``POST /v1/documents/resolve`` and return the JSON body."""
        return self._post_json(
            "/v1/documents/resolve", {"url": url, "formats": formats}
        )

    def assemble(
        self,
        *,
        query: str,
        urls: list[str],
        max_tokens: int,
        response_format: str = "detailed",
        exclude_chunk_ids: list[str] | None = None,
        version: str | None = None,
        compact: bool = False,
    ) -> dict[str, Any]:
        """Call ``POST /v1/context/assemble`` and return the JSON body.

        ``version`` and ``compact`` are sent only when set, so a default call
        sends exactly the body it sent before they existed.
        """
        payload: dict[str, Any] = {
            "query": query,
            "urls": urls,
            "max_tokens": max_tokens,
            "response_format": response_format,
            "exclude_chunk_ids": exclude_chunk_ids or [],
        }
        if version:
            payload["version"] = version
        if compact:
            payload["compact"] = True
        return self._post_json("/v1/context/assemble", payload)

    def site_index(self, url: str, *, max_tokens: int = 2000) -> dict[str, Any]:
        """Call ``POST /v1/sites/index`` and return the JSON body."""
        return self._post_json(
            "/v1/sites/index", {"url": url, "max_tokens": max_tokens}
        )

    def get_document(
        self, document_id: str, *, version_id: str | None = None
    ) -> dict[str, Any]:
        """Call ``GET /v1/documents/{document_id}`` and return the JSON body."""
        # The id is interpolated into the request PATH: an agent steered by a
        # page could otherwise send "../usage" and reach another endpoint with
        # the user's key (audit B2). Ids have a fixed shape (SPEC §4).
        if not _DOCUMENT_ID.fullmatch(document_id):
            raise ValueError("document_id must look like doc_<hex>")
        if version_id is not None and not _VERSION_ID.fullmatch(version_id):
            raise ValueError("version_id must look like ver_<hex>")
        params: dict[str, str] = {}
        if version_id is not None:
            params["version_id"] = version_id
        response = self._client.get(
            f"/v1/documents/{quote(document_id, safe='')}", params=params
        )
        response.raise_for_status()
        return _as_json_object(response)

    def _post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        """POST ``payload`` as JSON to ``path`` and return the decoded body."""
        response = self._client.post(path, json=payload)
        response.raise_for_status()
        return _as_json_object(response)


_DOCUMENT_ID = re.compile(r"doc_[0-9a-f]{1,64}")
_VERSION_ID = re.compile(r"ver_[0-9a-f]{1,64}")


def _as_json_object(response: httpx.Response) -> dict[str, Any]:
    """Decode a response body expected to be a JSON object."""
    body: Any = response.json()
    if not isinstance(body, dict):
        raise ValueError("expected a JSON object from the NINA API")
    return body
