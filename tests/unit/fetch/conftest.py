"""No-network guard and shared fixtures for the SSRF-sensitive fetch suite.

The autouse ``_no_network`` fixture monkeypatches the socket entry points so any
fetch test that attempts a real outbound connection fails loudly. It is scoped
to this package deliberately: the M0 health tests elsewhere use FastAPI's
TestClient over a loopback socket and must not be affected. Tests that genuinely
need a local loopback server opt out with ``@pytest.mark.allow_network``.
"""

from __future__ import annotations

import socket
from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def _no_network(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail any fetch test that opens a real network connection.

    Opt out with ``@pytest.mark.allow_network`` for loopback-server tests.
    """
    if request.node.get_closest_marker("allow_network") is not None:
        return

    def _blocked(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("real network access attempted in a test")

    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(socket, "getaddrinfo", _blocked)
    monkeypatch.setattr(socket.socket, "connect", _blocked, raising=True)


@pytest.fixture
def free_tcp_port() -> Iterator[int]:
    """Bind an ephemeral port, release it, and yield the number (loopback tests)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    yield port
