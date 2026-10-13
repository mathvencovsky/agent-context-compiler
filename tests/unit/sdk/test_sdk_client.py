"""Offline tests for the NINA SDK client (FEAT-004, SPEC §8).

No external network: the :class:`NinaClient` is driven by an
``httpx.MockTransport`` that answers the local API routes. The tests assert the
Bearer header is sent, responses are parsed into the typed models, a 429 with
``Retry-After`` triggers a retry then succeeds, and a §8 error envelope is raised
as the matching typed error (``UnauthorizedError`` / ``RateLimitedError``).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
from nina_client import (
    NinaClient,
    RateLimitedError,
    UnauthorizedError,
)

_API_KEY = "nina_test_key"
_BEARER = f"Bearer {_API_KEY}"

_RESOLVE_PAYLOAD: dict[str, Any] = {
    "document_id": "doc_abc",
    "version_id": "ver_abc",
    "source": {
        "url": "https://docs.example.com/guide",
        "final_url": "https://docs.example.com/guide",
        "content_type": "text/html",
        "fetched_at": "2026-10-14T00:00:00Z",
        "render_mode": "http",
    },
    "metadata": {"title": "Guide"},
    "markdown": "# Guide\n\nText.",
    "blocks": None,
    "stats": {
        "blocks": 1,
        "tokens_markdown": 4,
        "tokens_raw_html": 12,
        "cache_hit": False,
    },
    "warnings": [],
    "trust": "untrusted_source",
}

_ASSEMBLE_PAYLOAD: dict[str, Any] = {
    "query": "Which header authenticates API requests?",
    "fragments": [
        {
            "chunk_id": "chk_0001",
            "document_id": "doc_abc",
            "version_id": "ver_abc",
            "source_url": "https://docs.example.com/auth",
            "heading_path": ["Authentication"],
            "md": "Send the key in the Authorization header.",
            "tokens": 9,
            "scores": {"rrf": 0.032, "rerank": 0.91},
            "evidence": [
                {
                    "block_id": "b_0001",
                    "page": None,
                    "quote": "Send the key in the Authorization header.",
                }
            ],
        }
    ],
    "sources": [
        {
            "url": "https://docs.example.com/auth",
            "document_id": "doc_abc",
            "version_id": "ver_abc",
            "fetched_at": "2026-10-14T00:00:00Z",
            "status": "ok",
        }
    ],
    "token_usage": {
        "output_tokens": 9,
        "budget_tokens": 4000,
        "tokenizer": "cl100k_base (approximate)",
    },
    "coverage": "sufficient",
    "warnings": [],
    "trust": "untrusted_source",
}


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> NinaClient:
    return NinaClient(
        "http://nina.local",
        _API_KEY,
        transport=httpx.MockTransport(handler),
        sleep=lambda _seconds: None,  # no real delay in tests
    )


def test_resolve_sends_bearer_and_parses_typed_response() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers["Authorization"]
        seen["path"] = request.url.path
        return httpx.Response(200, json=_RESOLVE_PAYLOAD)

    client = _client(handler)
    result = client.resolve("https://docs.example.com/guide", formats=["markdown"])

    assert seen["auth"] == _BEARER
    assert seen["path"] == "/v1/documents/resolve"
    assert result.document_id == "doc_abc"
    assert result.source.render_mode == "http"
    assert result.stats.blocks == 1
    assert result.markdown == "# Guide\n\nText."
    assert result.trust == "untrusted_source"


def test_assemble_sends_bearer_and_parses_fragments() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == _BEARER
        assert request.url.path == "/v1/context/assemble"
        return httpx.Response(200, json=_ASSEMBLE_PAYLOAD)

    client = _client(handler)
    result = client.assemble(
        "Which header authenticates API requests?",
        ["https://docs.example.com/auth"],
        max_tokens=4000,
    )

    assert len(result.fragments) == 1
    fragment = result.fragments[0]
    assert fragment.source_url == "https://docs.example.com/auth"
    assert fragment.document_id == "doc_abc"
    assert fragment.scores.rerank == 0.91
    assert fragment.evidence[0].quote.startswith("Send the key")
    assert result.sources[0].status == "ok"
    assert result.token_usage.output_tokens == 9
    assert result.coverage == "sufficient"


def test_get_document_sends_bearer_and_version_param() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers["Authorization"]
        seen["path"] = request.url.path
        seen["version"] = request.url.params.get("version_id")
        return httpx.Response(200, json=_RESOLVE_PAYLOAD)

    client = _client(handler)
    result = client.get_document("doc_abc", version_id="ver_abc")

    assert seen["auth"] == _BEARER
    assert seen["path"] == "/v1/documents/doc_abc"
    assert seen["version"] == "ver_abc"
    assert result.version_id == "ver_abc"


def test_rate_limited_then_success_retries_and_honors_retry_after() -> None:
    calls = {"n": 0}
    delays: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(
                429,
                headers={"Retry-After": "2"},
                json={
                    "error": {
                        "code": "rate_limited",
                        "message": "slow down",
                        "details": {"retry_after": 2},
                    }
                },
            )
        return httpx.Response(200, json=_ASSEMBLE_PAYLOAD)

    client = NinaClient(
        "http://nina.local",
        _API_KEY,
        transport=httpx.MockTransport(handler),
        sleep=delays.append,
    )
    result = client.assemble("q", ["https://docs.example.com/auth"])

    assert calls["n"] == 2  # one 429, then one success
    assert delays == [2.0]  # honored the Retry-After header, not the backoff
    assert result.coverage == "sufficient"


def test_unauthorized_envelope_raises_typed_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={
                "error": {
                    "code": "unauthorized",
                    "message": "missing or invalid API key",
                    "details": {},
                }
            },
        )

    client = _client(handler)
    try:
        client.resolve("https://docs.example.com/guide")
    except UnauthorizedError as exc:
        assert exc.code == "unauthorized"
        assert exc.status_code == 401
    else:  # pragma: no cover - the call must raise
        raise AssertionError("expected UnauthorizedError")


def test_persistent_rate_limited_raises_with_retry_after() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(
            429,
            headers={"Retry-After": "5"},
            json={
                "error": {
                    "code": "rate_limited",
                    "message": "quota exhausted",
                    "details": {"retry_after": 5},
                }
            },
        )

    client = NinaClient(
        "http://nina.local",
        _API_KEY,
        max_retries=2,
        transport=httpx.MockTransport(handler),
        sleep=lambda _seconds: None,
    )
    try:
        client.assemble("q", ["https://docs.example.com/auth"])
    except RateLimitedError as exc:
        assert exc.code == "rate_limited"
        assert exc.retry_after == 5.0
        assert exc.status_code == 429
        assert calls["n"] == 3  # initial attempt + 2 retries
    else:  # pragma: no cover - the call must raise
        raise AssertionError("expected RateLimitedError")
