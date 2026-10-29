"""No-network guard for the rerank provider suite.

The provider tests must never touch the network (CLAUDE.md): the fake is pure,
and the local adapter is driven by an httpx.MockTransport. This
autouse fixture mirrors ``tests/unit/providers/llm/conftest.py`` — it
monkeypatches the socket entry points so any accidental outbound connection
fails loudly. Scoped to this package so it cannot affect the loopback suites.
"""

from __future__ import annotations

import socket

import pytest


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail any provider test that opens a real network connection."""

    def _blocked(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("real network access attempted in a test")

    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(socket, "getaddrinfo", _blocked)
    monkeypatch.setattr(socket.socket, "connect", _blocked, raising=True)
