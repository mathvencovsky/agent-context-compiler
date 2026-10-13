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

from math import ceil

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


class Unauthorized(FetchError):
    """A /v1 request lacked a valid active API key (SPEC §8 unauthorized=401).

    Raised by :func:`app.api.auth.require_api_key` when the ``Authorization:
    Bearer <key>`` header is missing/malformed, or the presented key is unknown
    or revoked. The ``unauthorized`` code is already in the SPEC §8 table; it
    subclasses :class:`FetchError` so it serializes through the same handler.
    """

    code = "unauthorized"
    http_status = 401


class RateLimited(FetchError):
    """A /v1 request exceeded the per-key rate limit or render quota (SPEC §8).

    Raised by the FEAT-002 quota dependency BEFORE the handler runs when the
    per-key token bucket is empty, or when the per-key daily render quota is
    exhausted (the render quota reuses this ``rate_limited`` code — a documented
    choice, DECISIONS.md). The ``retry_after`` seconds (always > 0) are carried
    on the instance AND mirrored into ``details`` so the global handler can emit
    the ``Retry-After`` header; the ``rate_limited`` code is already in the SPEC
    §8 table.
    """

    code = "rate_limited"
    http_status = 429

    def __init__(
        self,
        message: str,
        *,
        retry_after: float,
        details: dict[str, object] | None = None,
    ) -> None:
        merged: dict[str, object] = {"retry_after": retry_after}
        if details:
            merged.update(details)
        super().__init__(message, details=merged)
        self.retry_after = retry_after


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
    headers: dict[str, str] | None = None
    retry_after = exc.details.get("retry_after")
    if isinstance(retry_after, (int, float)):
        # SPEC §8 / RFC 9110: Retry-After is a non-negative integer of seconds.
        headers = {"Retry-After": str(max(0, ceil(retry_after)))}
    return JSONResponse(
        status_code=exc.http_status,
        content={
            "error": {
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
            }
        },
        headers=headers,
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
