"""``stats.tokens_raw_html`` is stored per version, not recounted (M10b).

Responses served from a stored version (cache hit, 304, same-hash dedup, GET
/v1/documents/{id}) used to download the raw body from S3 and tokenize all of
it just to fill ``stats.tokens_raw_html``. The count is now stored when the
version is created (migration 0007). These tests pin that the number every
path returns is EXACTLY what it was before (the UTF-8 decoding of the stored
raw bytes on the stored paths, the extraction's decoded text on the creating
path) and that the stored paths no longer read the raw blob.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import update

from app.index.tokens import count_tokens
from app.store.models import DocumentVersion
from tests.integration.conftest import Harness, html_response, read_html_fixture

pytestmark = pytest.mark.integration

_PATH = "/big"
_HTML = read_html_fixture("pydantic_changelog.html")
#: A Latin-1 page: its charset-decoded text differs from a UTF-8 decoding.
_LATIN1 = (
    "<html><head><meta charset='iso-8859-1'><title>Caf\xe9</title></head>"
    "<body><main><h1>Caf\xe9 cr\xe8me</h1>"
    + "<p>R\xe9sum\xe9 na\xefve fa\xe7ade d\xe9j\xe0 vu.</p>" * 40
    + "</main></body></html>"
).encode("latin-1")


def _resolve(harness: Harness, path: str = _PATH) -> dict[str, Any]:
    response = harness.client.post(
        "/v1/documents/resolve", json={"url": harness.url(path)}
    )
    assert response.status_code == 200, response.text
    return response.json()


def _forbid_raw_reads(monkeypatch: pytest.MonkeyPatch, blob_store: Any) -> None:
    def no_raw(version_id: str) -> bytes:
        raise AssertionError(f"raw blob of {version_id} read on a stored path")

    monkeypatch.setattr(blob_store, "get_raw", no_raw)


def _stored(harness: Harness, version_id: str) -> int | None:
    with harness.session_factory() as session:
        version = session.get(DocumentVersion, version_id)
        assert version is not None
        return version.tokens_raw_html


def test_count_is_stored_at_creation_and_served_on_hit_without_raw_read(
    harness: Harness, blob_store: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness.transport.set(_PATH, lambda: html_response(_HTML))
    expected = count_tokens(_HTML.decode("utf-8", errors="replace"))

    first = _resolve(harness)
    assert first["stats"]["tokens_raw_html"] == expected
    assert _stored(harness, first["version_id"]) == expected

    _forbid_raw_reads(monkeypatch, blob_store)
    hit = _resolve(harness)
    assert hit["stats"]["cache_hit"] is True
    assert hit["stats"]["tokens_raw_html"] == expected

    got = harness.client.get(f"/v1/documents/{first['document_id']}")
    assert got.status_code == 200, got.text
    assert got.json()["stats"]["tokens_raw_html"] == expected


def test_dedup_path_uses_the_stored_count(
    harness: Harness, blob_store: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Past the TTL, the same bytes again: dedup serves the stored count."""
    harness.transport.set(_PATH, lambda: html_response(_HTML))
    first = _resolve(harness)
    harness.clock.advance(harness.settings.resolve_cache_ttl_seconds + 1)

    _forbid_raw_reads(monkeypatch, blob_store)
    again = _resolve(harness)
    assert again["version_id"] == first["version_id"]
    assert again["stats"]["cache_hit"] is False
    assert again["stats"]["tokens_raw_html"] == first["stats"]["tokens_raw_html"]


def test_non_utf8_page_keeps_both_historical_numbers(harness: Harness) -> None:
    """The creating response counts the charset-decoded text; stored paths
    count the UTF-8 decoding of the raw bytes. Both stay what they were."""
    harness.transport.set(
        "/latin",
        lambda: html_response(
            _LATIN1, headers={"content-type": "text/html; charset=iso-8859-1"}
        ),
    )
    decoded = _LATIN1.decode("iso-8859-1")
    as_utf8 = _LATIN1.decode("utf-8", errors="replace")
    assert count_tokens(decoded) != count_tokens(as_utf8)

    first = _resolve(harness, "/latin")
    assert first["stats"]["tokens_raw_html"] == count_tokens(decoded)
    assert _stored(harness, first["version_id"]) == count_tokens(as_utf8)

    hit = _resolve(harness, "/latin")
    assert hit["stats"]["tokens_raw_html"] == count_tokens(as_utf8)


def test_row_without_stored_count_falls_back_to_the_blob(harness: Harness) -> None:
    """A version created before migration 0007 (NULL) counts from the raw
    blob, giving the same number as a stored count."""
    harness.transport.set(_PATH, lambda: html_response(_HTML))
    first = _resolve(harness)
    with harness.session_factory() as session:
        session.execute(
            update(DocumentVersion)
            .where(DocumentVersion.id == first["version_id"])
            .values(tokens_raw_html=None)
        )
        session.commit()

    hit = _resolve(harness)
    assert hit["stats"]["cache_hit"] is True
    assert hit["stats"]["tokens_raw_html"] == first["stats"]["tokens_raw_html"]
