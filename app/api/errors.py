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


class AllSourcesFailed(FetchError):
    """Every URL in an assemble request failed to resolve (SPEC §8).

    ``POST /v1/context/assemble`` keeps a partial failure at 200 (the surviving
    sources still produce a context), but when NONE of the URLs resolve there is
    no content to return. SPEC §8's table has no code for "every source failed",
    so a dedicated ``all_sources_failed`` -> 422 is recorded in DECISIONS.md
    (2026-10-13) rather than silently reusing ``extraction_empty`` (whose
    meaning is a single document that extracted to nothing). It subclasses
    :class:`FetchError` so it serializes through the same handler.
    """

    code = "all_sources_failed"
    http_status = 422


class AblationNotAllowed(FetchError):
    """An assemble ablation flag was sent while ``ENV == "production"`` (SPEC §6).

    The ablation flags (``use_lexical`` / ``use_vector`` / ``use_rerank`` /
    ``selection``) exist for benchmarking and are accepted only outside
    production; sending a non-default value in production is a bad request.
    """

    code = "ablation_not_allowed"
    http_status = 400


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
