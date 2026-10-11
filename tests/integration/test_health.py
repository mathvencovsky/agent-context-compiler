"""Integration test: GET /health against real Postgres + object storage (M0).

Requires the compose/CI services (Postgres + SeaweedFS) up and the bucket
created. Marked `integration` so the service-free smoke run skips it.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.mark.integration
def test_health_ok_with_services_up() -> None:
    client = TestClient(create_app())
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": "ok", "blobs": "ok"}
