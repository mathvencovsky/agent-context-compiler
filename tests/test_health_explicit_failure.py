"""Regression tests for the /health explicit-failure rule (M0).

CLAUDE.md non-negotiable: /health must never return 200 while a dependency is
broken. These tests force each dependency check to fail and assert the exact
503 body, in both directions and when both fail. They patch the check helpers
so no Postgres/S3 services are required (not marked `integration`).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.main import create_app


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def test_health_503_when_db_down(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(main, "_check_db", lambda _s: "error")
    monkeypatch.setattr(main, "_check_blobs", lambda _s: "ok")
    response = client.get("/health")
    assert response.status_code == 503
    assert response.json() == {"status": "error", "db": "error", "blobs": "ok"}


def test_health_503_when_blobs_down(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(main, "_check_db", lambda _s: "ok")
    monkeypatch.setattr(main, "_check_blobs", lambda _s: "error")
    response = client.get("/health")
    assert response.status_code == 503
    assert response.json() == {"status": "error", "db": "ok", "blobs": "error"}


def test_health_503_when_both_down(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(main, "_check_db", lambda _s: "error")
    monkeypatch.setattr(main, "_check_blobs", lambda _s: "error")
    response = client.get("/health")
    assert response.status_code == 503
    assert response.json() == {"status": "error", "db": "error", "blobs": "error"}


def test_health_200_when_all_ok(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(main, "_check_db", lambda _s: "ok")
    monkeypatch.setattr(main, "_check_blobs", lambda _s: "ok")
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": "ok", "blobs": "ok"}
