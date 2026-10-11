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
from sqlalchemy import create_engine, text

from app.config import Settings, get_settings
from app.logging import RequestIdMiddleware, configure_logging, logger

CheckStatus = Literal["ok", "error"]


class HealthResponse(TypedDict):
    """Exact shape of the GET /health body."""

    status: CheckStatus
    db: CheckStatus
    blobs: CheckStatus


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

    app = FastAPI(title="NINA", version="0.1.0")
    app.add_middleware(RequestIdMiddleware)

    @app.get("/health")
    def health() -> JSONResponse:
        settings = get_settings()
        db = _check_db(settings)
        blobs = _check_blobs(settings)
        status: CheckStatus = "ok" if db == "ok" and blobs == "ok" else "error"
        body: HealthResponse = {"status": status, "db": db, "blobs": blobs}
        status_code = 200 if status == "ok" else 503
        return JSONResponse(content=body, status_code=status_code)

    return app


app = create_app()
