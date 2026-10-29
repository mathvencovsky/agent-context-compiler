"""M10b agent features over HTTP: ``compact``, ``version``, ``/v1/sites/index``.

Same harness as ``test_assemble_api.py`` (real Postgres and S3, scripted HTTP,
fake providers). The default-call test proves that sending the new fields at
their defaults changes nothing in the response.
"""

from __future__ import annotations

import httpx
import pytest

from app.index.tokens import count_tokens
from app.retrieve.compact import ELISION
from tests.integration.conftest import Harness, html_response, read_html_fixture
from tests.integration.test_assemble_api import (  # noqa: F401 - fixture import
    AssembleHarness,
    assemble_harness,
)

pytestmark = pytest.mark.integration

_HTML = read_html_fixture("python_tutorial_classes.html")
_QUERY = "How are class instances and methods defined?"


def _body(harness: AssembleHarness, path: str, **extra: object) -> dict[str, object]:
    return {
        "query": _QUERY,
        "urls": [harness.url(path)],
        "max_tokens": 4000,
        "selection": "truncate",
        **extra,
    }


def test_defaults_of_the_new_fields_change_nothing(
    assemble_harness: AssembleHarness,  # noqa: F811
) -> None:
    harness = assemble_harness
    harness.transport.set("/classes", lambda: html_response(_HTML))
    plain = harness.client.post("/v1/context/assemble", json=_body(harness, "/classes"))
    explicit = harness.client.post(
        "/v1/context/assemble",
        json=_body(harness, "/classes", version=None, compact=False),
    )
    assert plain.status_code == explicit.status_code == 200
    assert plain.json() == explicit.json()


def test_compact_keeps_verbatim_runs_within_budget(
    assemble_harness: AssembleHarness,  # noqa: F811
) -> None:
    harness = assemble_harness
    harness.transport.set("/classes", lambda: html_response(_HTML))
    whole = harness.client.post(
        "/v1/context/assemble", json=_body(harness, "/classes", max_tokens=32000)
    ).json()
    md_by_chunk = {f["chunk_id"]: f["md"] for f in whole["fragments"]}

    response = harness.client.post(
        "/v1/context/assemble",
        json=_body(harness, "/classes", max_tokens=300, compact=True),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["token_usage"]["budget_tokens"] == 300
    assert 0 < body["token_usage"]["output_tokens"] <= 300
    assert body["fragments"]
    for frag in body["fragments"]:
        assert frag["tokens"] == count_tokens(frag["md"])
        source = md_by_chunk[frag["chunk_id"]]
        for run in frag["md"].split("\n"):
            if run == ELISION or not run.strip():
                continue
            assert run in source  # kept text is never altered
    codes = [h["code"] for h in body["hints"]]
    assert "compacted" in codes

    concise = harness.client.post(
        "/v1/context/assemble",
        json=_body(
            harness, "/classes", max_tokens=300, compact=True, response_format="concise"
        ),
    ).json()
    assert "- compacted: " in concise["context"]


def test_version_mismatch_hint(
    assemble_harness: AssembleHarness,  # noqa: F811
) -> None:
    harness = assemble_harness
    harness.transport.set("/3.11/classes", lambda: html_response(_HTML))
    other = harness.client.post(
        "/v1/context/assemble", json=_body(harness, "/3.11/classes", version="3.12")
    ).json()
    hint = next(h for h in other["hints"] if h["code"] == "version_mismatch")
    assert hint["details"] == {
        "requested": "3.12",
        "found": {"docs.example.com": ["3.11"]},
    }
    same = harness.client.post(
        "/v1/context/assemble", json=_body(harness, "/3.11/classes", version="3.11")
    ).json()
    assert all(h["code"] != "version_mismatch" for h in same["hints"])


def test_version_is_validated(assemble_harness: AssembleHarness) -> None:  # noqa: F811
    harness = assemble_harness
    response = harness.client.post(
        "/v1/context/assemble",
        json=_body(harness, "/classes", version="3.12\n</nina_notes>"),
    )
    assert response.status_code == 422


def test_site_index_route(harness: Harness) -> None:
    harness.transport.set(
        "/llms.txt",
        lambda: httpx.Response(
            200,
            content=b"# Docs\n\n## Guides\n\n- [Start](/start.md): first steps\n",
            headers={"content-type": "text/plain; charset=utf-8"},
        ),
    )
    response = harness.client.post(
        "/v1/sites/index", json={"url": harness.url("/guide/page")}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["index_url"] == harness.url("/llms.txt")
    assert body["entries"] == [
        {
            "section": "Guides",
            "title": "Start",
            "url": harness.url("/start.md"),
            "note": "first steps",
        }
    ]
    assert body["trust"] == "untrusted_source"


def test_site_index_blocked_destination_is_an_error(harness: Harness) -> None:
    response = harness.client.post(
        "/v1/sites/index", json={"url": "http://127.0.0.1/docs"}
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] in {"blocked_destination", "invalid_url"}
