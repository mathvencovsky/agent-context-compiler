"""Integration tests for every SPEC §8 error POST /v1/documents/resolve emits.

Each case drives the resolve path to a specific typed error through the
injected transport / resolver / robots stub (HTTP is never real) and asserts
BOTH the exact HTTP status AND the SPEC §8 envelope shape
``{"error": {"code", "message", "details"}}``.
"""

from __future__ import annotations

import httpx
import pytest

from tests.integration.conftest import (
    BLOCKED_HOST,
    Harness,
    html_response,
    read_pdf_fixture,
)

pytestmark = pytest.mark.integration

_PATH = "/guide"
_TEXTLESS_PDF = read_pdf_fixture("textless.pdf")


def _assert_error_envelope(response: httpx.Response, code: str, status: int) -> None:
    assert response.status_code == status, response.text
    payload = response.json()
    assert set(payload) == {"error"}
    error = payload["error"]
    assert set(error) == {"code", "message", "details"}
    assert error["code"] == code
    assert isinstance(error["message"], str) and error["message"]
    assert isinstance(error["details"], dict)


def test_invalid_url(harness: Harness) -> None:
    """A non-http(s) scheme is rejected by the guard (invalid_url, 400)."""
    response = harness.client.post(
        "/v1/documents/resolve", json={"url": "ftp://docs.example.com/guide"}
    )
    _assert_error_envelope(response, "invalid_url", 400)


def test_blocked_destination(harness: Harness) -> None:
    """A host resolving to a private IP is refused (blocked_destination, 400)."""
    response = harness.client.post(
        "/v1/documents/resolve", json={"url": f"http://{BLOCKED_HOST}/guide"}
    )
    _assert_error_envelope(response, "blocked_destination", 400)


def test_robots_disallowed(harness: Harness) -> None:
    """robots.txt Disallow: / forbids the fetch (robots_disallowed, 403)."""
    harness.transport.robots_body = b"User-agent: *\nDisallow: /\n"
    harness.transport.set(_PATH, lambda: html_response(b"<html><body>x</body></html>"))

    response = harness.client.post(
        "/v1/documents/resolve", json={"url": harness.url(_PATH)}
    )
    _assert_error_envelope(response, "robots_disallowed", 403)


def test_too_large(harness: Harness) -> None:
    """A Content-Length over the limit aborts the fetch (too_large, 413)."""
    limit = harness.settings.max_body_bytes

    def oversized() -> httpx.Response:
        return httpx.Response(
            200,
            content=b"<html></html>",
            headers={
                "content-type": "text/html",
                "content-length": str(limit + 1),
            },
        )

    harness.transport.set(_PATH, oversized)
    response = harness.client.post(
        "/v1/documents/resolve", json={"url": harness.url(_PATH)}
    )
    _assert_error_envelope(response, "too_large", 413)


def test_unsupported_content_type(harness: Harness) -> None:
    """A non-HTML/PDF content-type is rejected (unsupported_content_type, 415)."""
    harness.transport.set(
        _PATH,
        lambda: httpx.Response(
            200, content=b"\x89PNG\r\n", headers={"content-type": "image/png"}
        ),
    )
    response = harness.client.post(
        "/v1/documents/resolve", json={"url": harness.url(_PATH)}
    )
    _assert_error_envelope(response, "unsupported_content_type", 415)


def test_fetch_failed(harness: Harness) -> None:
    """A transport connect error surfaces as fetch_failed (502)."""

    def boom() -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    harness.transport.set(_PATH, boom)
    response = harness.client.post(
        "/v1/documents/resolve", json={"url": harness.url(_PATH)}
    )
    _assert_error_envelope(response, "fetch_failed", 502)


def test_timeout(harness: Harness) -> None:
    """A transport read timeout surfaces as timeout (504)."""

    def slow() -> httpx.Response:
        raise httpx.ReadTimeout("read timed out")

    harness.transport.set(_PATH, slow)
    response = harness.client.post(
        "/v1/documents/resolve", json={"url": harness.url(_PATH)}
    )
    _assert_error_envelope(response, "timeout", 504)


def test_extraction_empty(harness: Harness) -> None:
    """A textless PDF yields no content -> extraction_empty (422)."""
    harness.transport.set(
        _PATH,
        lambda: httpx.Response(
            200, content=_TEXTLESS_PDF, headers={"content-type": "application/pdf"}
        ),
    )
    response = harness.client.post(
        "/v1/documents/resolve", json={"url": harness.url(_PATH)}
    )
    _assert_error_envelope(response, "extraction_empty", 422)
