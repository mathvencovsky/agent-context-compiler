"""Metadata and JSON-LD extraction (SPEC §4, FEAT-002)."""

from __future__ import annotations

from datetime import UTC, datetime

from app.extract.ir import SourceInfo
from app.extract.jsonld import extract_metadata


def _source() -> SourceInfo:
    return SourceInfo(
        url="https://docs.example.com/auth",
        final_url="https://docs.example.com/auth",
        content_type="text/html; charset=utf-8",
        fetched_at=datetime(2026, 10, 11, 12, 0, tzinfo=UTC),
        etag=None,
        last_modified=None,
        content_hash="a" * 64,
        render_mode="http",
    )


def test_title_from_title_tag_wins() -> None:
    html = (
        "<html><head><title>Real Title</title>"
        '<meta property="og:title" content="OG Title"></head><body></body></html>'
    )
    meta = extract_metadata(html, _source())
    assert meta.title == "Real Title"


def test_title_falls_back_to_og_then_jsonld() -> None:
    og_html = (
        '<html><head><meta property="og:title" content="OG Title">'
        "</head><body></body></html>"
    )
    assert extract_metadata(og_html, _source()).title == "OG Title"

    jsonld_html = (
        '<html><head><script type="application/ld+json">'
        '{"@type":"Article","headline":"LD Headline"}</script>'
        "</head><body></body></html>"
    )
    assert extract_metadata(jsonld_html, _source()).title == "LD Headline"


def test_lang_from_html_attr_then_meta() -> None:
    html_attr = '<html lang="pt-BR"><head></head><body></body></html>'
    assert extract_metadata(html_attr, _source()).lang == "pt-BR"

    meta_lang = (
        '<html><head><meta http-equiv="content-language" content="de">'
        "</head><body></body></html>"
    )
    assert extract_metadata(meta_lang, _source()).lang == "de"


def test_published_and_modified_from_meta() -> None:
    html = (
        "<html><head>"
        '<meta property="article:published_time" content="2024-01-02T03:04:05Z">'
        '<meta property="article:modified_time" content="2024-05-06T07:08:09Z">'
        "</head><body></body></html>"
    )
    meta = extract_metadata(html, _source())
    assert meta.published_at == "2024-01-02T03:04:05Z"
    assert meta.modified_at == "2024-05-06T07:08:09Z"


def test_dates_fall_back_to_jsonld() -> None:
    html = (
        '<html><head><script type="application/ld+json">'
        '{"@type":"Article","datePublished":"2020-01-01",'
        '"dateModified":"2020-02-02"}</script></head><body></body></html>'
    )
    meta = extract_metadata(html, _source())
    assert meta.published_at == "2020-01-01"
    assert meta.modified_at == "2020-02-02"


def test_jsonld_preserved_unchanged() -> None:
    block = (
        '{"@context":"https://schema.org","@type":"Article",'
        '"headline":"H","author":{"@type":"Person","name":"Ada"},'
        '"keywords":["a","b"]}'
    )
    html = (
        '<html><head><script type="application/ld+json">'
        + block
        + "</script></head><body></body></html>"
    )
    meta = extract_metadata(html, _source())
    assert len(meta.jsonld) == 1
    ld = meta.jsonld[0]
    assert ld["@type"] == "Article"
    assert ld["author"] == {"@type": "Person", "name": "Ada"}
    assert ld["keywords"] == ["a", "b"]


def test_empty_document_yields_none_fields() -> None:
    meta = extract_metadata("", _source())
    assert meta.title is None
    assert meta.lang is None
    assert meta.published_at is None
    assert meta.modified_at is None
    assert meta.jsonld == []


def test_bytes_decoded_with_content_type_charset() -> None:
    source = _source()
    raw = "<html><head><title>Café</title></head><body></body></html>".encode()
    assert extract_metadata(raw, source).title == "Café"
