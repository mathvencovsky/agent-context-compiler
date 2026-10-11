"""OpenAPI contract test — both endpoints and schema examples (SPEC §8).

This test only builds the app and reads the generated schema; it touches no DB
or S3, so it is intentionally LEFT UNMARKED (no ``integration`` marker) and runs
in the fast gate too — the OpenAPI surface is part of the always-on contract.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app


def _schema() -> dict:
    with TestClient(create_app()) as client:
        response = client.get("/openapi.json")
        assert response.status_code == 200
        return response.json()


def test_openapi_lists_both_paths() -> None:
    paths = _schema()["paths"]
    assert "/v1/documents/resolve" in paths
    assert "/v1/documents/{document_id}" in paths
    assert "post" in paths["/v1/documents/resolve"]
    assert "get" in paths["/v1/documents/{document_id}"]


def test_openapi_request_schema_has_examples() -> None:
    schemas = _schema()["components"]["schemas"]
    assert "ResolveRequest" in schemas
    request_schema = schemas["ResolveRequest"]
    # Examples attached via model_config json_schema_extra and the url Field.
    assert "examples" in request_schema or _has_field_example(request_schema, "url")


def test_openapi_response_schema_has_examples() -> None:
    schemas = _schema()["components"]["schemas"]
    assert "ResolveResponse" in schemas
    assert "examples" in schemas["ResolveResponse"]


def _has_field_example(schema: dict, field: str) -> bool:
    properties = schema.get("properties", {})
    field_schema = properties.get(field, {})
    return "examples" in field_schema or "example" in field_schema
