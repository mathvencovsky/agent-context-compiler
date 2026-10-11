"""Root test configuration.

The no-network guard lives in ``tests/unit/fetch/conftest.py`` so it only
governs the SSRF-sensitive fetch suite; it must NOT block the M0 health tests,
which legitimately use FastAPI's TestClient over a loopback socket. Only the
shared ``allow_network`` marker is registered here so the whole suite can
reference it.
"""

from __future__ import annotations

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "allow_network: test may open a real loopback socket; skips no-network guard",
    )
