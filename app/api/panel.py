"""The local usage panel: ``GET /painel`` and its data, ``GET /painel/dados``.

For one person on their own machine (DECISIONS 2026-11-04): it exists only where
local use is recorded (ENV=dev + ``record_local_usage``), answers only to a
loopback ``Host`` (a page on another site cannot reach it by DNS rebinding), and
stays out of ``/v1``, so polling it is neither recorded nor rate limited.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from app.api.usage import (
    UsageSessionFactoryDep,
    UsageSettingsDep,
    records_local_usage,
)
from app.usage.events import UsageEventRepo, panel_row

_PAGE = Path(__file__).with_name("panel.html").read_text(encoding="utf-8")
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost"})
#: More than a person reaches in months of daily use; the page shows the newest.
_MAX_EVENTS = 5_000
_PAGE_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": (
        "default-src 'none'; connect-src 'self'; "
        "script-src 'unsafe-inline'; style-src 'unsafe-inline'"
    ),
}


def _local_only(request: Request, settings: UsageSettingsDep) -> None:
    host = request.headers.get("host", "").rsplit(":", 1)[0]
    if not records_local_usage(settings) or host not in _LOOPBACK_HOSTS:
        raise HTTPException(status_code=404)


router = APIRouter(dependencies=[Depends(_local_only)], include_in_schema=False)


@router.get("/painel")
def panel_page() -> HTMLResponse:
    """The page; it polls :func:`panel_data`."""
    return HTMLResponse(_PAGE, headers=_PAGE_HEADERS)


@router.get("/painel/dados")
def panel_data(
    session_factory: UsageSessionFactoryDep, settings: UsageSettingsDep
) -> dict[str, Any]:
    """Local calls, newest first. Holds no question text: none is ever recorded."""
    session = session_factory()
    try:
        rows = UsageEventRepo(session).recent(_MAX_EVENTS)
        return {
            "version": settings.nina_version,
            "events": [panel_row(row) for row in rows],
        }
    finally:
        session.close()
