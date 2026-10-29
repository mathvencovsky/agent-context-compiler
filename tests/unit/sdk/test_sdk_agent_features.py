"""SDK surface of the M10b agent features: site index, version, compact."""

from __future__ import annotations

import json
from typing import Any

import httpx
from nina_client import NinaClient, SiteIndexResponse

_CONCISE = {
    "context": "ctx",
    "sources": [],
    "token_usage": {"output_tokens": 0, "budget_tokens": 4000},
    "coverage": "insufficient",
    "warnings": [],
    "trust": "untrusted_source",
}


def _client(sent: list[dict[str, Any]], answer: dict[str, Any]) -> NinaClient:
    def handler(request: httpx.Request) -> httpx.Response:
        sent.append({"path": request.url.path, **json.loads(request.content)})
        return httpx.Response(200, json=answer)

    return NinaClient(
        "http://nina.local", "nina_k", transport=httpx.MockTransport(handler)
    )


def test_default_assemble_bodies_are_unchanged() -> None:
    sent: list[dict[str, Any]] = []
    client = _client(sent, {**_CONCISE, "query": "q", "fragments": []})
    client.assemble("q", ["https://d.example/a"])
    client.assemble_concise("q", ["https://d.example/a"])
    for body in sent:
        assert "version" not in body and "compact" not in body


def test_version_and_compact_are_sent_when_set() -> None:
    sent: list[dict[str, Any]] = []
    client = _client(sent, _CONCISE)
    client.assemble_concise(
        "q", ["https://d.example/a"], max_tokens=800, version="3.12", compact=True
    )
    assert sent[0]["version"] == "3.12" and sent[0]["compact"] is True


def test_site_index_parses_entries() -> None:
    sent: list[dict[str, Any]] = []
    answer = {
        "index_url": "https://d.example/llms.txt",
        "title": "D",
        "summary": "",
        "entries": [
            {"section": "Docs", "title": "A", "url": "https://d.example/a", "note": ""}
        ],
        "entries_total": 1,
        "context": "site index: 1 of 1 links\n",
        "warnings": [],
        "trust": "untrusted_source",
    }
    result = _client(sent, answer).site_index("https://d.example/guide")
    assert sent == [
        {
            "path": "/v1/sites/index",
            "url": "https://d.example/guide",
            "max_tokens": 2000,
        }
    ]
    assert isinstance(result, SiteIndexResponse)
    assert result.entries[0].url == "https://d.example/a"
    assert result.index_url == "https://d.example/llms.txt"
    assert result.trust == "untrusted_source"


def test_site_index_without_llms_txt() -> None:
    answer = {
        "index_url": None,
        "title": "",
        "summary": "",
        "entries": [],
        "entries_total": 0,
        "context": "x",
        "warnings": [{"code": "llms_txt_not_found"}],
    }
    result = _client([], answer).site_index("https://d.example/")
    assert result.index_url is None and result.warnings[0]["code"] == (
        "llms_txt_not_found"
    )
