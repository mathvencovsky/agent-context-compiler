"""Conditional revalidation of a stale version (SPEC §5, M10b).

Past the TTL, resolve sends the stored ``ETag`` / ``Last-Modified`` back as
``If-None-Match`` / ``If-Modified-Since``. A ``304`` serves the stored version
exactly as the full GET of unchanged bytes did (same-hash dedup); anything else
takes the full-GET path. The upstream here is a small HTTP-semantics server
behind the harness's mocked transport (no network).
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from tests.integration.conftest import Harness, read_html_fixture

pytestmark = pytest.mark.integration

_PATH = "/guide"
_HTML_A = read_html_fixture("python_tutorial_classes.html")
_HTML_B = read_html_fixture("pydantic_models.html")
_ETAG = '"v1"'
_LAST_MODIFIED = "Tue, 27 Oct 2026 10:00:00 GMT"


class Upstream:
    """Serves one page with validators and honours conditional requests."""

    def __init__(self) -> None:
        self.body = _HTML_A
        self.etag: str | None = _ETAG
        self.last_modified: str | None = _LAST_MODIFIED
        self.page_requests: list[httpx.Request] = []
        self.redirect_to: str | None = None
        self.always_304 = False

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, content=b"User-agent: *\nAllow: /\n")
        self.page_requests.append(request)
        if request.url.path == _PATH and self.redirect_to is not None:
            return httpx.Response(302, headers={"location": self.redirect_to})
        headers = {"content-type": "text/html; charset=utf-8"}
        if self.etag:
            headers["etag"] = self.etag
        if self.last_modified:
            headers["last-modified"] = self.last_modified
        inm = request.headers.get("if-none-match")
        ims = request.headers.get("if-modified-since")
        if self.always_304 or (
            (inm is not None and inm == self.etag)
            or (inm is None and ims is not None and ims == self.last_modified)
        ):
            return httpx.Response(304, headers=headers)
        return httpx.Response(200, content=self.body, headers=headers)


@pytest.fixture
def upstream(harness: Harness) -> Upstream:
    server = Upstream()
    harness.transport._handle = server.handle  # type: ignore[method-assign]
    return server


def _resolve(harness: Harness, *, force_refresh: bool = False) -> dict[str, Any]:
    response = harness.client.post(
        "/v1/documents/resolve",
        json={"url": harness.url(_PATH), "force_refresh": force_refresh},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _expire(harness: Harness) -> None:
    harness.clock.advance(harness.settings.resolve_cache_ttl_seconds + 1)


def _strip_volatile(body: dict[str, Any]) -> dict[str, Any]:
    body = dict(body)
    body["source"] = {k: v for k, v in body["source"].items() if k != "fetched_at"}
    body["stats"] = {k: v for k, v in body["stats"].items() if k != "cache_hit"}
    return body


def test_stale_version_is_revalidated_and_304_serves_it(
    harness: Harness, upstream: Upstream
) -> None:
    first = _resolve(harness)
    assert "if-none-match" not in upstream.page_requests[0].headers

    _expire(harness)
    second = _resolve(harness)

    request = upstream.page_requests[-1]
    assert request.headers["if-none-match"] == _ETAG
    assert request.headers["if-modified-since"] == _LAST_MODIFIED
    assert second["version_id"] == first["version_id"]
    assert second["stats"]["cache_hit"] is False
    # Same document, provenance, formats and stats as before.
    assert _strip_volatile(second) == _strip_volatile(first)

    # fetched_at was touched: the next call within the TTL is a cache hit.
    third = _resolve(harness)
    assert third["stats"]["cache_hit"] is True
    assert len(upstream.page_requests) == 2


def test_304_answer_equals_the_full_get_answer(
    harness: Harness, upstream: Upstream
) -> None:
    """The response after a 304 is the one the old full GET gave for the same
    unchanged bytes (same-hash dedup), field by field."""
    _resolve(harness)
    _expire(harness)
    revalidated = _resolve(harness)

    # Same state, but the upstream ignores validators: the full-GET path.
    upstream.etag = '"other"'  # never matches the stored "v1"
    _expire(harness)
    full_get = _resolve(harness)
    assert full_get["version_id"] == revalidated["version_id"]
    assert _strip_volatile(full_get) == _strip_volatile(revalidated)


def test_changed_page_is_fetched_in_full(harness: Harness, upstream: Upstream) -> None:
    first = _resolve(harness)
    upstream.body, upstream.etag = _HTML_B, '"v2"'
    _expire(harness)
    second = _resolve(harness)
    assert upstream.page_requests[-1].headers["if-none-match"] == _ETAG
    assert second["version_id"] != first["version_id"]


def test_last_modified_alone_is_sent(harness: Harness, upstream: Upstream) -> None:
    upstream.etag = None
    first = _resolve(harness)
    _expire(harness)
    second = _resolve(harness)
    request = upstream.page_requests[-1]
    assert "if-none-match" not in request.headers
    assert request.headers["if-modified-since"] == _LAST_MODIFIED
    assert second["version_id"] == first["version_id"]


def test_no_validators_means_an_unconditional_get(
    harness: Harness, upstream: Upstream
) -> None:
    upstream.etag = upstream.last_modified = None
    _resolve(harness)
    _expire(harness)
    _resolve(harness)
    request = upstream.page_requests[-1]
    assert "if-none-match" not in request.headers
    assert "if-modified-since" not in request.headers


def test_force_refresh_never_revalidates(harness: Harness, upstream: Upstream) -> None:
    _resolve(harness)
    _resolve(harness, force_refresh=True)
    request = upstream.page_requests[-1]
    assert "if-none-match" not in request.headers
    assert "if-modified-since" not in request.headers


def test_304_from_another_final_url_is_not_trusted(
    harness: Harness, upstream: Upstream
) -> None:
    """The page now redirects and the new target answers 304 to the stored
    validators: they were issued for another URL, so resolve refetches
    without them and extracts the new target."""
    first = _resolve(harness)
    _expire(harness)
    upstream.redirect_to = harness.url("/moved")
    upstream.always_304 = True
    calls_before = len(upstream.page_requests)

    def handle(request: httpx.Request) -> httpx.Response:
        conditional = "if-none-match" in request.headers
        upstream.always_304 = conditional
        upstream.body = _HTML_B
        return Upstream.handle(upstream, request)

    harness.transport._handle = handle  # type: ignore[method-assign]
    second = _resolve(harness)

    paths = [
        (r.url.path, "if-none-match" in r.headers)
        for r in upstream.page_requests[calls_before:]
    ]
    assert paths == [(_PATH, True), ("/moved", True), (_PATH, False), ("/moved", False)]
    assert second["version_id"] != first["version_id"]
    assert second["source"]["final_url"] == harness.url("/moved")
