"""Integration tests for every SPEC §8 error POST /v1/documents/resolve emits.

Each case drives the resolve path to a specific typed error through the
injected transport / resolver / robots stub (HTTP is never real) and asserts
BOTH the exact HTTP status AND the SPEC §8 envelope shape
``{"error": {"code", "message", "details", "request_id"}}``, the request id
being the same as the ``X-Request-ID`` response header.
"""

from __future__ import annotations

import re

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
    assert set(error) == {"code", "message", "details", "request_id"}
    assert error["request_id"] == response.headers["X-Request-ID"]
    assert re.fullmatch(r"[0-9a-f]{32}", error["request_id"])
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


@pytest.mark.parametrize(
    "declared", ["application/octet-stream", "binary/octet-stream", "text/plain", ""]
)
def test_pdf_with_wrong_content_type_is_extracted(
    harness: Harness, declared: str
) -> None:
    """A PDF body (``%PDF-``) under a wrong or missing type is still a PDF.

    It is extracted as a PDF (200, page provenance) and the mismatch is said
    in ``warnings`` (G L2), instead of failing with unsupported_content_type.
    """
    pdf = read_pdf_fixture("api_reference.pdf")
    headers = {"content-type": declared} if declared else {}
    harness.transport.set(
        _PATH, lambda: httpx.Response(200, content=pdf, headers=headers)
    )
    response = harness.client.post(
        "/v1/documents/resolve",
        json={"url": harness.url(_PATH), "formats": ["blocks"]},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    codes = [w["code"] for w in body["warnings"]]
    assert "content_type_mismatch" in codes
    assert body["blocks"] and all(b["page"] >= 1 for b in body["blocks"])


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


# O1: an error page is never a document (jina-ai/reader#1103,
# firecrawl/firecrawl#2316). The bodies are real HTML, so they would extract.
_ERROR_PAGE = (
    b"<html><head><title>Page not found</title></head><body><main>"
    + b"<h1>Page not found</h1>"
    + b"<p>Sorry, the page you were looking for does not exist anymore.</p>" * 5
    + b"</main></body></html>"
)


@pytest.mark.parametrize(
    ("status", "headers"),
    [(404, {}), (500, {}), (302, {}), (410, {})],
    ids=["404", "500", "302-without-location", "410"],
)
def test_error_status_is_upstream_status_and_stores_nothing(
    harness: Harness, status: int, headers: dict[str, str]
) -> None:
    def error_page() -> httpx.Response:
        return httpx.Response(
            status,
            content=_ERROR_PAGE,
            headers={"content-type": "text/html; charset=utf-8", **headers},
        )

    harness.transport.set(_PATH, error_page)
    response = harness.client.post(
        "/v1/documents/resolve", json={"url": harness.url(_PATH)}
    )

    _assert_error_envelope(response, "upstream_status", 502)
    assert response.json()["error"]["details"]["upstream_status"] == status
    with harness.session_factory() as session:
        from app.store.models import DocumentVersion

        assert session.query(DocumentVersion).count() == 0


def test_url_rejected_secret(harness: Harness) -> None:
    """A URL carrying a credential parameter is refused before any fetch."""
    harness.transport.set(_PATH, lambda: html_response(b"<html><body>x</body></html>"))
    response = harness.client.post(
        "/v1/documents/resolve",
        json={"url": harness.url(_PATH) + "?api_key=sk-live-0000"},
    )
    _assert_error_envelope(response, "url_rejected_secret", 400)
    assert "sk-live-0000" not in response.text
