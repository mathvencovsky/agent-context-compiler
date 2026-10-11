"""Smoke test: the app factory builds without any services running (M0)."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.routing import APIRoute

import app.main as main


def test_create_app_returns_fastapi_instance() -> None:
    app = main.create_app()
    assert isinstance(app, FastAPI)


def test_health_route_is_registered() -> None:
    app = main.create_app()
    paths = {route.path for route in app.routes if isinstance(route, APIRoute)}
    assert "/health" in paths


def test_module_level_app_is_fastapi() -> None:
    assert isinstance(main.app, FastAPI)
