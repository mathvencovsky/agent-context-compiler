"""Integration tests for GET /v1/documents/{document_id} — read from storage.

A GET always reads the stored IR from S3 and never refetches: the transport
call counter must be unchanged across a GET. Missing documents/versions return
the SPEC §8 ``not_found`` envelope (404).
"""

from __future__ import annotations

import pytest

from tests.integration.conftest import Harness, html_response, read_html_fixture

pytestmark = pytest.mark.integration

_PATH = "/guide"
_HTML = read_html_fixture("python_tutorial_classes.html")


def _resolve(harness: Harness) -> dict:
    harness.transport.set(_PATH, lambda: html_response(_HTML))
    response = harness.client.post(
        "/v1/documents/resolve", json={"url": harness.url(_PATH)}
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_get_document_from_storage_no_refetch(harness: Harness) -> None:
    """GET returns the stored document with the transport counter unchanged."""
    resolved = _resolve(harness)
    document_id = resolved["document_id"]
    calls_after_resolve = harness.transport.calls

    response = harness.client.get(f"/v1/documents/{document_id}")
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["document_id"] == document_id
    assert body["version_id"] == resolved["version_id"]
    assert body["stats"]["cache_hit"] is True
    assert body["markdown"] == resolved["markdown"]
    # No refetch: the GET touched storage only.
    assert harness.transport.calls == calls_after_resolve


def test_get_document_with_explicit_version(harness: Harness) -> None:
    """GET with an explicit version_id returns that version from storage."""
    resolved = _resolve(harness)
    document_id = resolved["document_id"]
    version_id = resolved["version_id"]
    calls_after_resolve = harness.transport.calls

    response = harness.client.get(
        f"/v1/documents/{document_id}", params={"version_id": version_id}
    )
    assert response.status_code == 200, response.text
    assert response.json()["version_id"] == version_id
    assert harness.transport.calls == calls_after_resolve


def test_get_document_unknown_id_not_found(harness: Harness) -> None:
    """GET of an unknown document id returns the not_found envelope (404)."""
    response = harness.client.get("/v1/documents/doc_doesnotexist00")
    assert response.status_code == 404, response.text
    error = response.json()["error"]
    assert error["code"] == "not_found"
    assert isinstance(error["details"], dict)


def test_get_document_unknown_version_not_found(harness: Harness) -> None:
    """GET with an unknown version_id for a real document returns 404."""
    resolved = _resolve(harness)
    document_id = resolved["document_id"]

    response = harness.client.get(
        f"/v1/documents/{document_id}", params={"version_id": "ver_nope0000000000"}
    )
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "not_found"
