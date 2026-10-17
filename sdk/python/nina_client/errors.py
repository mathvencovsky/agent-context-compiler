"""Typed client errors mapping the NINA SPEC §8 error table — M9.

Every NINA error response is the SPEC §8 envelope
``{"error": {"code", "message", "details"}}``. :func:`error_from_envelope`
turns that envelope into the matching :class:`NinaError` subclass keyed by the
machine ``code``, so a caller can ``except RateLimitedError`` / ``except
UnauthorizedError`` instead of inspecting status codes by hand. Unknown codes
fall back to the :class:`NinaError` base (never silently swallowed).

The subclasses mirror the server-side typed errors (``app/fetch/errors.py`` +
``app/api/errors.py``): the ``code`` constant on each class is the stable string
from the §8 table. :class:`RateLimitedError` additionally carries ``retry_after``
read from ``details`` (the server mirrors the ``Retry-After`` seconds there).
"""

from __future__ import annotations

from typing import Any


class NinaError(Exception):
    """Base class for every error returned by the NINA API (SPEC §8 envelope).

    Carries the machine ``code``, the human ``message`` and the ``details`` dict
    exactly as the server sent them, plus the HTTP ``status_code`` when known.
    Subclasses set the ``code`` class attribute to the SPEC §8 string they map.
    """

    #: Stable machine code from the SPEC §8 table; overridden per subclass.
    code: str = "nina_error"

    def __init__(
        self,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details: dict[str, Any] = details or {}
        self.status_code = status_code

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code!r}, message={self.message!r})"


class InvalidURLError(NinaError):
    """URL is malformed or uses a forbidden scheme (SPEC §8 ``invalid_url``)."""

    code = "invalid_url"


class BlockedDestinationError(NinaError):
    """Destination resolves to a non-global IP or disallowed port (SPEC §8)."""

    code = "blocked_destination"


class RobotsDisallowedError(NinaError):
    """``robots.txt`` forbids fetching the path (SPEC §8 ``robots_disallowed``)."""

    code = "robots_disallowed"


class RobotsUnavailableError(NinaError):
    """``robots.txt`` could not be fetched; fail closed (SPEC §8)."""

    code = "robots_unavailable"


class TooLargeError(NinaError):
    """Response body exceeded the configured size limit (SPEC §8 ``too_large``)."""

    code = "too_large"


class UnsupportedContentTypeError(NinaError):
    """Content type is neither HTML nor PDF (SPEC §8 ``unsupported_content_type``)."""

    code = "unsupported_content_type"


class ExtractionEmptyError(NinaError):
    """Extraction produced no usable content (SPEC §8 ``extraction_empty``)."""

    code = "extraction_empty"


class FetchFailedError(NinaError):
    """Transport-level failure reaching the source (SPEC §8 ``fetch_failed``)."""

    code = "fetch_failed"


class UpstreamStatusError(NinaError):
    """The site answered with an HTTP error instead of the page (SPEC §8
    ``upstream_status``; ``details["upstream_status"]`` has the status)."""

    code = "upstream_status"


class TimeoutError(NinaError):  # noqa: A001 — the SPEC §8 code is literally "timeout"
    """Connect or total timeout was exceeded (SPEC §8 ``timeout``)."""

    code = "timeout"


class NotFoundError(NinaError):
    """A stored document or version does not exist (SPEC §8 ``not_found``)."""

    code = "not_found"


class AllSourcesFailedError(NinaError):
    """Every URL in an assemble request failed (SPEC §8 ``all_sources_failed``)."""

    code = "all_sources_failed"


class AblationNotAllowedError(NinaError):
    """An ablation flag was sent in production (SPEC §8 ``ablation_not_allowed``)."""

    code = "ablation_not_allowed"


class UnauthorizedError(NinaError):
    """The request lacked a valid active API key (SPEC §8 ``unauthorized``)."""

    code = "unauthorized"


class RateLimitedError(NinaError):
    """The per-key rate limit or render quota was exceeded (SPEC §8 ``rate_limited``).

    ``retry_after`` is the number of seconds to wait before retrying, read from
    the response ``Retry-After`` header (preferred) or the ``details.retry_after``
    the server mirrors into the envelope. ``None`` when the server sent neither.
    """

    code = "rate_limited"

    def __init__(
        self,
        message: str,
        *,
        retry_after: float | None = None,
        details: dict[str, Any] | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message, details=details, status_code=status_code)
        self.retry_after = retry_after


#: Registry mapping each SPEC §8 code to its typed error class.
_ERROR_BY_CODE: dict[str, type[NinaError]] = {
    cls.code: cls
    for cls in (
        InvalidURLError,
        BlockedDestinationError,
        RobotsDisallowedError,
        RobotsUnavailableError,
        TooLargeError,
        UnsupportedContentTypeError,
        ExtractionEmptyError,
        FetchFailedError,
        UpstreamStatusError,
        TimeoutError,
        NotFoundError,
        AllSourcesFailedError,
        AblationNotAllowedError,
        UnauthorizedError,
        RateLimitedError,
    )
}


def error_from_envelope(
    body: Any,
    *,
    status_code: int,
    retry_after: float | None = None,
) -> NinaError:
    """Build the typed :class:`NinaError` for a SPEC §8 error ``body``.

    ``body`` is the decoded JSON response. A well-formed error carries
    ``{"error": {"code", "message", "details"}}``; anything else degrades to a
    generic :class:`NinaError` describing the status (never silently ignored).
    For a rate-limit, the ``retry_after`` seconds are taken from the header first
    (passed in) and otherwise from ``details.retry_after``.
    """
    error_obj = body.get("error") if isinstance(body, dict) else None
    if not isinstance(error_obj, dict):
        return NinaError(
            f"NINA API returned HTTP {status_code} with an unexpected body",
            status_code=status_code,
        )

    code = str(error_obj.get("code", "nina_error"))
    message = str(error_obj.get("message", f"HTTP {status_code}"))
    raw_details = error_obj.get("details")
    details: dict[str, Any] = raw_details if isinstance(raw_details, dict) else {}

    cls = _ERROR_BY_CODE.get(code, NinaError)
    if cls is RateLimitedError:
        resolved = retry_after
        if resolved is None:
            candidate = details.get("retry_after")
            if isinstance(candidate, (int, float)):
                resolved = float(candidate)
        return RateLimitedError(
            message,
            retry_after=resolved,
            details=details,
            status_code=status_code,
        )
    return cls(message, details=details, status_code=status_code)
