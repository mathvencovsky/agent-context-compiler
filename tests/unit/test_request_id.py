"""X-Request-ID header and ``error.request_id`` (guide §11/§13, SPEC §8).

A minimal app with the real middleware and the real exception handlers, so the
mechanism is tested without Postgres or S3. The full app is covered by
``tests/integration/test_resolve_errors.py``.
"""

from __future__ import annotations

import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.errors import (
    NotFound,
    RateLimited,
    fetch_error_handler,
    generic_exception_handler,
    missing_key_handler,
)
from app.fetch.errors import FetchError
from app.logging import REQUEST_ID_HEADER, RequestIdMiddleware
from app.providers.llm.openai import MissingAPIKey

_HEX32 = re.compile(r"[0-9a-f]{32}")


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)
    app.add_exception_handler(FetchError, fetch_error_handler)
    app.add_exception_handler(MissingAPIKey, missing_key_handler)
    app.add_exception_handler(Exception, generic_exception_handler)

    @app.get("/ok")
    def ok() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/not-found")
    def not_found() -> None:
        raise NotFound("document not found", details={"document_id": "doc_x"})

    @app.get("/limited")
    def limited() -> None:
        raise RateLimited("slow down", retry_after=2.5)

    @app.get("/no-key")
    def no_key() -> None:
        raise MissingAPIKey("set OPENAI_API_KEY.")

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("secret internal detail")

    return app


@pytest.fixture
def client() -> TestClient:
    return TestClient(_app(), raise_server_exceptions=False)


def test_success_carries_a_request_id_header(client: TestClient) -> None:
    response = client.get("/ok")
    assert response.status_code == 200
    assert _HEX32.fullmatch(response.headers[REQUEST_ID_HEADER])


def test_each_request_gets_its_own_id(client: TestClient) -> None:
    first = client.get("/ok").headers[REQUEST_ID_HEADER]
    second = client.get("/ok").headers[REQUEST_ID_HEADER]
    assert first != second


def test_client_supplied_request_id_is_ignored(client: TestClient) -> None:
    """The id is server-minted: a caller cannot inject text into the logs."""
    response = client.get("/ok", headers={REQUEST_ID_HEADER: "forged\nlog line"})
    assert _HEX32.fullmatch(response.headers[REQUEST_ID_HEADER])
    assert len(response.headers.get_list(REQUEST_ID_HEADER)) == 1


@pytest.mark.parametrize(
    ("path", "status", "code"),
    [
        ("/not-found", 404, "not_found"),
        ("/limited", 429, "rate_limited"),
        ("/no-key", 503, "provider_not_configured"),
        ("/boom", 500, "internal_error"),
    ],
)
def test_error_envelope_carries_the_same_request_id(
    client: TestClient, path: str, status: int, code: str
) -> None:
    response = client.get(path)
    assert response.status_code == status
    error = response.json()["error"]
    assert error["code"] == code
    header = response.headers[REQUEST_ID_HEADER]
    assert _HEX32.fullmatch(header)
    assert error["request_id"] == header
    # The SPEC §8 fields are unchanged; the id never goes into details.
    assert set(error) == {"code", "message", "details", "request_id"}
    assert "request_id" not in error["details"]
    assert len(response.headers.get_list(REQUEST_ID_HEADER)) == 1


def test_rate_limited_keeps_retry_after(client: TestClient) -> None:
    response = client.get("/limited")
    assert response.headers["Retry-After"] == "3"
    assert REQUEST_ID_HEADER in response.headers


def test_internal_error_does_not_leak(client: TestClient) -> None:
    response = client.get("/boom")
    assert "secret internal detail" not in response.text
