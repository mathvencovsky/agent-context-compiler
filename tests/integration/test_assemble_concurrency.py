"""assemble resolves its URLs concurrently with the sequential output (M10b).

The same request is run with ``assemble_fetch_concurrency=1`` (the original
one-by-one loop) and with the concurrent default, each on an empty database
with the chunk id sequence restarted. The two responses must be identical byte
for byte — sources, warnings, fragments, chunk ids, scores, evidence — in both
response formats, while the concurrent run takes less wall time and never has
more than ``assemble_per_host_concurrency`` requests open on one host.
"""

from __future__ import annotations

import threading
import time
from collections import Counter
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.api import assemble, documents
from app.providers.embeddings.fake import FakeEmbeddingProvider
from app.providers.rerank.fake import FakeRerankProvider
from tests.integration.conftest import (
    BLOCKED_HOST,
    FIXTURE_HOST,
    FIXTURE_PUBLIC_IP,
    Harness,
    _truncate,
    read_html_fixture,
)

pytestmark = pytest.mark.integration

_OTHER_HOST = "docs2.example.com"
_PAGES = {
    "/classes": read_html_fixture("python_tutorial_classes.html"),
    "/models": read_html_fixture("pydantic_models.html"),
    "/first": read_html_fixture("fastapi_first_steps.html"),
    "/empty": b"<html><body></body></html>",
}
_DELAY_S = 0.25


class SlowUpstream:
    """Serves the pages after a delay and records per-host concurrency."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.open: Counter[str] = Counter()
        self.peak: Counter[str] = Counter()

    def handle(self, request: httpx.Request) -> httpx.Response:
        host = request.headers["host"]
        if request.url.path == "/robots.txt":
            return httpx.Response(200, content=b"User-agent: *\nAllow: /\n")
        with self.lock:
            self.open[host] += 1
            self.peak[host] = max(self.peak[host], self.open[host])
        try:
            time.sleep(_DELAY_S)
            body = _PAGES.get(request.url.path)
            if body is None:
                return httpx.Response(404, content=b"nope")
            return httpx.Response(
                200, content=body, headers={"content-type": "text/html; charset=utf-8"}
            )
        finally:
            with self.lock:
                self.open[host] -= 1


def _urls() -> list[str]:
    a, b = f"http://{FIXTURE_HOST}", f"http://{_OTHER_HOST}"
    return [
        f"{a}/classes",
        f"{b}/models",
        f"{a}/classes",  # duplicate: second one is a cache hit
        f"{a}/missing",  # 404 -> upstream_status, fails alone
        f"http://{BLOCKED_HOST}/x",  # private IP -> blocked_destination
        f"{b}/empty",  # resolves, no text -> empty_content warning
        f"{a}/first",
        f"{a}/classes#install",  # same document as /classes (fragment)
        f"http://{FIXTURE_HOST.upper()}/models",  # host case: same document
    ]


def _run(
    harness: Harness, engine: Engine, concurrency: int, fmt: str
) -> tuple[dict[str, Any], float, SlowUpstream]:
    _truncate(engine)
    with engine.begin() as conn:
        conn.execute(text("ALTER SEQUENCE chunks_id_seq RESTART WITH 1"))
    upstream = SlowUpstream()
    harness.transport._handle = upstream.handle  # type: ignore[method-assign]
    settings = harness.settings.model_copy(
        update={"assemble_fetch_concurrency": concurrency}
    )
    app = harness.client.app
    app.dependency_overrides[documents.provide_settings] = lambda: settings  # type: ignore[attr-defined]
    started = time.monotonic()
    response = harness.client.post(
        "/v1/context/assemble",
        json={
            "query": "How are classes and models defined?",
            "urls": _urls(),
            "max_tokens": 3000,
            "selection": "truncate",
            "response_format": fmt,
        },
    )
    elapsed = time.monotonic() - started
    assert response.status_code == 200, response.text
    return response.json(), elapsed, upstream


@pytest.fixture
def asm(harness: Harness) -> Harness:
    harness.resolver._mapping[_OTHER_HOST] = [FIXTURE_PUBLIC_IP]  # type: ignore[attr-defined]
    app = harness.client.app
    app.dependency_overrides[assemble.provide_embeddings] = FakeEmbeddingProvider  # type: ignore[attr-defined]
    app.dependency_overrides[assemble.provide_reranker] = FakeRerankProvider  # type: ignore[attr-defined]
    return harness


@pytest.mark.parametrize("fmt", ["detailed", "concise"])
def test_concurrent_output_is_identical_to_sequential(
    asm: Harness, engine: Engine, fmt: str
) -> None:
    sequential, slow, upstream_seq = _run(asm, engine, 1, fmt)
    concurrent, fast, upstream = _run(asm, engine, 6, fmt)

    # Not vacuous: there is a context, built from several documents.
    if fmt == "detailed":
        assert len({f["document_id"] for f in sequential["fragments"]}) >= 2
    else:
        assert sequential["context"]
    assert concurrent == sequential
    assert max(upstream_seq.peak.values()) == 1
    assert max(upstream.peak.values()) <= asm.settings.assemble_per_host_concurrency
    assert fast < slow


def test_failures_stay_independent_and_ordered(asm: Harness, engine: Engine) -> None:
    body, _elapsed, _upstream = _run(asm, engine, 6, "detailed")
    statuses = [(s["url"], s["status"]) for s in body["sources"]]
    assert [u for u, _ in statuses] == _urls()
    by_url = dict(statuses)
    assert by_url[f"http://{FIXTURE_HOST}/missing"] == "upstream_status"
    assert by_url[f"http://{BLOCKED_HOST}/x"] == "blocked_destination"
    assert by_url[f"http://{_OTHER_HOST}/empty"] == "ok"
    codes = [w["code"] for w in body["warnings"]]
    assert "empty_content" in codes
    assert "upstream_status" in codes
    assert "blocked_destination" in codes
