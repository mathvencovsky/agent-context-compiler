"""Contract: no silent empty 200 (CLAUDE.md "falha explícita", guide §3).

Any ``200`` from ``resolve`` with no content blocks, and any ``200`` from
``assemble`` (detailed or concise) with zero fragments, carries at least one
warning or hint that says why. The cases drive each empty path the code has:
empty or chrome-only HTML, empty Markdown and text, a cache hit of such a page,
an assemble whose sources hold no text, whose query matches nothing, whose
candidates were all excluded, or whose budget fits nothing.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.store.models import Chunk
from tests.integration.conftest import Harness, html_response, read_html_fixture
from tests.integration.test_assemble_api import (  # noqa: F401 - fixture
    AssembleHarness,
    assemble_harness,
)

pytestmark = pytest.mark.integration

_HTML_A = read_html_fixture("python_tutorial_classes.html")


def assert_resolve_explicit(response: httpx.Response) -> None:
    """A 200 resolve with no blocks must say why in ``warnings``."""
    if response.status_code != 200:
        assert "error" in response.json()
        return
    body = response.json()
    if body["stats"]["blocks"] == 0:
        assert body["warnings"], "200 with no content blocks and no warning"
        assert all(w["code"] and w["message"] for w in body["warnings"])
    if body.get("blocks") is not None:
        assert len(body["blocks"]) == body["stats"]["blocks"]


def assert_assemble_explicit(response: httpx.Response) -> dict[str, Any]:
    """A 200 assemble with zero fragments must say why (warnings or hints)."""
    assert response.status_code == 200, response.text
    body = response.json()
    empty = (
        not body["fragments"]
        if "fragments" in body
        else body["token_usage"]["output_tokens"] == 0
    )
    if empty:
        reasons = body["warnings"] + body["hints"]
        assert reasons, "200 with zero fragments and no warning or hint"
        assert all(r["code"] and r["message"] for r in reasons)
    return body


# --------------------------------------------------------------------------- #
# resolve
# --------------------------------------------------------------------------- #
_EMPTY_BODIES = {
    "empty-html": (b"", "text/html; charset=utf-8"),
    "whitespace-html": (b"  \n ", "text/html; charset=utf-8"),
    "comment-only-html": (b"<!-- nothing here -->", "text/html"),
    "empty-body-element": (b"<html><body></body></html>", "text/html"),
    "chrome-only": (
        b"<html><body><nav>Home Docs Blog</nav></body></html>",
        "text/html",
    ),
    "spa-shell": (b"<html><body><div id='root'></div></body></html>", "text/html"),
    "empty-markdown": (b"", "text/markdown"),
    "front-matter-only": (b"---\ntitle: x\n---\n", "text/markdown"),
    "blank-text": (b"   \n\n", "text/plain"),
}


@pytest.mark.parametrize("case", sorted(_EMPTY_BODIES))
@pytest.mark.parametrize(
    "formats", [["markdown", "blocks"], ["markdown"], ["blocks"], []]
)
def test_resolve_never_returns_a_silent_empty_200(
    harness: Harness, case: str, formats: list[str]
) -> None:
    content, content_type = _EMPTY_BODIES[case]
    harness.transport.set(
        "/p",
        lambda: httpx.Response(
            200, content=content, headers={"content-type": content_type}
        ),
    )
    payload = {"url": harness.url("/p"), "formats": formats, "render": "never"}
    first = harness.client.post("/v1/documents/resolve", json=payload)
    assert first.status_code in (200, 422), first.text
    assert_resolve_explicit(first)
    # The cache hit serves the stored IR: the warning must survive storage.
    second = harness.client.post("/v1/documents/resolve", json=payload)
    assert_resolve_explicit(second)
    if first.status_code == 200:
        assert second.json()["stats"]["cache_hit"] is True
        assert second.json()["warnings"] == first.json()["warnings"]


def test_empty_html_body_is_an_explicit_empty_document_not_a_500(
    harness: Harness,
) -> None:
    """A 200 with an empty HTML body used to crash readability (500)."""
    harness.transport.set("/p", lambda: html_response(b""))
    response = harness.client.post(
        "/v1/documents/resolve", json={"url": harness.url("/p"), "render": "never"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["blocks"] == []
    assert [w["code"] for w in body["warnings"]] == ["empty_content"]


def test_get_document_of_an_empty_page_keeps_the_warning(harness: Harness) -> None:
    harness.transport.set("/p", lambda: html_response(b"<html><body></body></html>"))
    resolved = harness.client.post(
        "/v1/documents/resolve", json={"url": harness.url("/p"), "render": "never"}
    ).json()
    got = harness.client.get(f"/v1/documents/{resolved['document_id']}")
    assert_resolve_explicit(got)
    assert got.json()["warnings"]


# --------------------------------------------------------------------------- #
# assemble (detailed and concise)
# --------------------------------------------------------------------------- #
_FORMATS = ["detailed", "concise"]


@pytest.fixture
def asm(assemble_harness: AssembleHarness) -> AssembleHarness:  # noqa: F811
    """The assemble harness of ``test_assemble_api`` under a short name."""
    return assemble_harness


def _assemble(harness: AssembleHarness, **body: Any) -> httpx.Response:
    return harness.client.post("/v1/context/assemble", json=body)  # type: ignore[return-value]


@pytest.mark.parametrize("response_format", _FORMATS)
def test_assemble_over_a_page_without_text_says_so(
    asm: AssembleHarness, response_format: str
) -> None:
    harness = asm
    harness.transport.set(
        "/empty", lambda: html_response(b"<html><body></body></html>")
    )
    body = assert_assemble_explicit(
        _assemble(
            harness,
            query="how are classes defined",
            urls=[harness.url("/empty")],
            response_format=response_format,
        )
    )
    assert body["sources"][0]["status"] == "ok"
    # The reason is the page, not just a low relevance: say it is empty.
    empty = [w for w in body["warnings"] if w["code"] == "empty_content"]
    assert empty and empty[0]["url"] == harness.url("/empty")


@pytest.mark.parametrize("response_format", _FORMATS)
def test_assemble_with_no_lexical_match_and_no_vector_says_so(
    asm: AssembleHarness, response_format: str
) -> None:
    harness = asm
    harness.transport.set("/a", lambda: html_response(_HTML_A))
    assert_assemble_explicit(
        _assemble(
            harness,
            query="zzqx vvwp",
            urls=[harness.url("/a")],
            use_vector=False,
            response_format=response_format,
        )
    )


@pytest.mark.parametrize("response_format", _FORMATS)
def test_assemble_with_every_candidate_excluded_says_so(
    asm: AssembleHarness,
    session_factory: sessionmaker[Session],
    response_format: str,
) -> None:
    harness = asm
    harness.transport.set("/a", lambda: html_response(_HTML_A))
    first = _assemble(
        harness, query="How are classes defined?", urls=[harness.url("/a")]
    ).json()
    assert first["fragments"]
    # Exclude every chunk of the page: no candidate is left.
    with session_factory() as session:
        all_ids = [
            str(i)
            for i in session.scalars(
                select(Chunk.id).where(
                    Chunk.version_id == first["sources"][0]["version_id"]
                )
            )
        ]
    assert_assemble_explicit(
        _assemble(
            harness,
            query="How are classes defined?",
            urls=[harness.url("/a")],
            exclude_chunk_ids=all_ids,
            response_format=response_format,
        )
    )


@pytest.mark.parametrize("response_format", _FORMATS)
@pytest.mark.parametrize("use_rerank", [True, False])
def test_assemble_whose_budget_fits_nothing_says_so(
    asm: AssembleHarness, response_format: str, use_rerank: bool
) -> None:
    harness = asm
    # One code block (never split below 800 tokens) larger than the smallest
    # budget (256 tokens), so no chunk fits.
    code = "\n".join(f"classes_define_objects({i}, {i + 1})" for i in range(60))
    page = f"<html><body><main><pre><code>{code}</code></pre></main></body></html>"
    harness.transport.set("/big", lambda: html_response(page.encode()))
    body = assert_assemble_explicit(
        _assemble(
            harness,
            query="classes define objects",
            urls=[harness.url("/big")],
            max_tokens=256,
            use_rerank=use_rerank,
            response_format=response_format,
        )
    )
    if "fragments" in body:
        assert body["fragments"] == []


@pytest.mark.parametrize("response_format", _FORMATS)
def test_assemble_partial_failure_says_which_source_failed(
    asm: AssembleHarness, response_format: str
) -> None:
    harness = asm
    harness.transport.set("/a", lambda: html_response(_HTML_A))
    body = assert_assemble_explicit(
        _assemble(
            harness,
            query="How are classes defined?",
            urls=[harness.url("/a"), harness.url("/missing")],
            response_format=response_format,
        )
    )
    assert any(w["code"] == "upstream_status" for w in body["warnings"])
    assert any(h["code"] == "source_failed" for h in body["hints"])
