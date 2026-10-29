"""ENV=dev disables auth: it must only serve loopback connections (audit A3).

Before: any connection, from any interface, was authenticated as the dev user
(no key, no rate limit). Now a connection that arrived on a non-loopback local
address is refused with ``unauthorized`` while ENV=dev.
"""

from __future__ import annotations

from typing import Any

import pytest
from starlette.requests import Request

from app.api.auth import require_api_key
from app.api.errors import Unauthorized
from app.config import Settings


def _request(server: Any) -> Request:
    scope = {"type": "http", "method": "GET", "path": "/", "headers": []}
    if server is not None:
        scope["server"] = server
    return Request(scope)


def _no_db() -> Any:
    raise AssertionError("dev mode must not touch the database")


@pytest.mark.parametrize(
    "server",
    [("127.0.0.1", 8000), ("::1", 8000), ("127.0.0.53", 80), ("testserver", 80), None],
)
def test_dev_mode_serves_loopback(server: Any) -> None:
    principal = require_api_key(
        _request(server),
        Settings(_env_file=None, env="dev"),
        _no_db,  # type: ignore[call-arg, arg-type]
    )
    assert principal.api_key is None


@pytest.mark.parametrize(
    "server",
    [("192.168.1.5", 8000), ("10.0.0.2", 80), ("2001:db8::5", 80), ("0.0.0.0", 80)],
)
def test_dev_mode_refuses_network_connections(server: Any) -> None:
    with pytest.raises(Unauthorized, match="ENV=dev"):
        require_api_key(
            _request(server),
            Settings(_env_file=None, env="dev"),
            _no_db,  # type: ignore[call-arg, arg-type]
        )
