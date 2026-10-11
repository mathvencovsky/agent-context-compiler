"""Prove the autouse no-network fixture actually blocks real connections."""

from __future__ import annotations

import socket

import pytest


def test_real_connection_is_blocked() -> None:
    with pytest.raises(AssertionError, match="real network access"):
        socket.create_connection(("example.com", 80))


def test_getaddrinfo_is_blocked() -> None:
    with pytest.raises(AssertionError, match="real network access"):
        socket.getaddrinfo("example.com", 80)
