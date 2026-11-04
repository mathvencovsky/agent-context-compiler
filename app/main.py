"""FastAPI application factory and GET /health (M0).

/health enforces the non-negotiable explicit-failure rule: it never returns 200
while a dependency is broken. Postgres and object storage are checked
independently so one failure never masks the other.
"""

from __future__ import annotations

from typing import Literal, TypedDict

import boto3
from botocore.config import Config as BotoConfig
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute, request_response
from sqlalchemy import create_engine, text

from app.api.assemble import router as assemble_router
from app.api.documents import router as documents_router
from app.api.errors import (
    fetch_error_handler,
    generic_exception_handler,
    missing_key_handler,
)
from app.api.panel import router as panel_router
from app.api.sites import router as sites_router
from app.api.usage import UsageMiddleware
from app.api.usage import router as usage_router
from app.config import Settings, get_settings
from app.fetch.errors import FetchError
from app.logging import RequestIdMiddleware, configure_logging, logger
from app.providers.llm.openai import MissingAPIKey

CheckStatus = Literal["ok", "error"]


class HealthResponse(TypedDict):
    """Exact shape of the GET /health body."""

    status: CheckStatus
    db: CheckStatus
    blobs: CheckStatus
    version: str


def _check_db(settings: Settings) -> CheckStatus:
    """Return 'ok' if a `SELECT 1` against Postgres succeeds, else 'error'."""
    try:
        engine = create_engine(
            settings.database_url,
            connect_args={"connect_timeout": settings.connect_timeout},
            pool_pre_ping=True,
        )
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        finally:
            engine.dispose()
    except Exception:
        logger.warning("health.db.error", exc_info=True)
        return "error"
    return "ok"


def _check_blobs(settings: Settings) -> CheckStatus:
    """Return 'ok' if head_bucket on the configured S3 bucket succeeds."""
    try:
        client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
            config=BotoConfig(
                connect_timeout=settings.connect_timeout,
                read_timeout=settings.total_timeout,
                retries={"max_attempts": 1},
            ),
        )
        client.head_bucket(Bucket=settings.s3_bucket)
    except Exception:
        logger.warning("health.blobs.error", exc_info=True)
        return "error"
    return "ok"


def create_app() -> FastAPI:
    """Build and return the FastAPI application."""
    configure_logging()

    if get_settings().env == "dev":
        # Loud on purpose: dev mode has no authentication and no rate limit,
        # and serves loopback connections only (app/api/auth.py, audit A3).
        logger.warning(
            "auth.disabled",
            reason="ENV=dev: no API keys, no rate limit; loopback connections only",
        )

    app = FastAPI(title="NINA", version="0.1.0")
    # Outermost first: the usage middleware records ONE event per /v1 request
    # (M9) and the request-id middleware binds the log context. add_middleware
    # stacks LIFO, so RequestIdMiddleware (added last) wraps UsageMiddleware and
    # runs first — the request_id is bound before usage touches the request.
    app.add_middleware(UsageMiddleware)
    app.add_middleware(RequestIdMiddleware)

    # Register the M5 routes directly on the app router so they appear flat in
    # ``app.routes`` (FastAPI 0.143 makes ``include_router`` a lazy
    # ``_IncludedRouter`` wrapper whose paths are not enumerable up front; the
    # resolve router carries no prefix, so extending is equivalent and keeps the
    # routes — and their OpenAPI schema — fully materialized).
    app.router.routes.extend(documents_router.routes)
    # The M7 assemble router mounts the same way (same reasoning as above).
    app.router.routes.extend(assemble_router.routes)
    # The M9 usage router (GET /v1/usage) mounts the same way.
    app.router.routes.extend(usage_router.routes)
    # The llms.txt site index (POST /v1/sites/index) mounts the same way.
    app.router.routes.extend(sites_router.routes)
    # The local usage panel, outside /v1 (DECISIONS.md 2026-11-04).
    app.router.routes.extend(panel_router.routes)
    # Extending the routes directly (above) keeps them enumerable in
    # ``app.routes``, but — unlike ``include_router`` — it leaves each route's
    # ``dependency_overrides_provider`` unset. The provider is captured when the
    # route handler is built (in ``APIRoute.__init__``), so ``app.dependency_
    # overrides`` would be silently ignored on the resolve/get-document routes.
    # Point the provider at the app AND rebuild the cached route handler so the
    # injectable ``Depends`` seams can actually be overridden (the integration
    # tests rely on this). No runtime behavior change when nothing is
    # overridden. See DECISIONS.md 2026-10-11.
    for route in (
        *documents_router.routes,
        *assemble_router.routes,
        *usage_router.routes,
        *sites_router.routes,
        *panel_router.routes,
    ):
        if isinstance(route, APIRoute):
            route.dependency_overrides_provider = app
            route.app = request_response(route.get_route_handler())
    app.add_exception_handler(FetchError, fetch_error_handler)
    app.add_exception_handler(MissingAPIKey, missing_key_handler)
    app.add_exception_handler(Exception, generic_exception_handler)

    @app.get("/health")
    def health() -> JSONResponse:
        settings = get_settings()
        db = _check_db(settings)
        blobs = _check_blobs(settings)
        status: CheckStatus = "ok" if db == "ok" and blobs == "ok" else "error"
        body: HealthResponse = {
            "status": status,
            "db": db,
            "blobs": blobs,
            "version": settings.nina_version,
        }
        status_code = 200 if status == "ok" else 503
        return JSONResponse(content=body, status_code=status_code)

    return app


app = create_app()
