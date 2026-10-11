"""Global exception handlers serializing the SPEC §8 error envelope — M5.

Every typed :class:`~app.fetch.errors.FetchError` already carries the SPEC §8
``code`` and ``http_status`` as attributes, so :func:`fetch_error_handler`
serializes the whole family with zero per-error casing. Any other uncaught
exception is turned into a generic ``500`` by :func:`generic_exception_handler`
WITHOUT leaking a stack trace or internal message.

``NotFound`` is a resolve-layer error for a GET of a document/version that was
never stored. SPEC §8's table describes fetch/extract failures and does not
cover "stored document not found", so the ``not_found`` code is recorded in
DECISIONS.md (2026-10-11) rather than invented as a content-level code. It
subclasses :class:`FetchError` so it serializes through the same handler.
"""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

from app.fetch.errors import FetchError


class NotFound(FetchError):
    """A requested stored document or version does not exist (GET only)."""

    code = "not_found"
    http_status = 404


def fetch_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Serialize a :class:`FetchError` into the SPEC §8 envelope.

    Registered for the ``FetchError`` base class, so every typed subclass
    (and :class:`NotFound`) is handled. Typed as ``Exception`` to satisfy the
    FastAPI/Starlette handler signature; a non-``FetchError`` would never be
    routed here.
    """
    assert isinstance(exc, FetchError)
    return JSONResponse(
        status_code=exc.http_status,
        content={
            "error": {
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
            }
        },
    )


def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Serialize any uncaught exception into a generic ``500`` (no leak)."""
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "internal_error",
                "message": "internal server error",
                "details": {},
            }
        },
    )
