"""Typed fetch errors — 1:1 with the SPEC §8 error table (M1).

Every error carries the ``code`` and ``http_status`` the SPEC assigns to it as
class attributes, plus a human ``message`` and an optional ``details`` dict. The
M5 global handler will serialize these into the SPEC §8 envelope
``{"error": {"code", "message", "details"}}`` without needing any per-error
special casing — the shape is fully described by the attributes here.
"""

from __future__ import annotations

from typing import Any


class FetchError(Exception):
    """Base class for every typed fetch error.

    Subclasses set ``code`` and ``http_status`` as class attributes. The base
    class is never raised directly; it exists so callers (and the M5 handler)
    can catch the whole family with a single ``except FetchError``.
    """

    #: Stable machine code from the SPEC §8 table (e.g. ``"blocked_destination"``).
    code: str = "fetch_error"
    #: HTTP status the SPEC §8 table maps this code to.
    http_status: int = 500

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details: dict[str, Any] = details or {}

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code!r}, message={self.message!r})"


class InvalidURL(FetchError):
    """URL is malformed, has a forbidden scheme, or is an IP in alternate notation."""

    code = "invalid_url"
    http_status = 400


class BlockedDestination(FetchError):
    """Destination resolves to a non-global IP or a disallowed port (SSRF guard)."""

    code = "blocked_destination"
    http_status = 400


class RobotsDisallowed(FetchError):
    """robots.txt forbids fetching this path for our user-agent (or ``*``)."""

    code = "robots_disallowed"
    http_status = 403


class RobotsUnavailable(FetchError):
    """robots.txt could not be fetched (5xx/timeout/blocked) — fail closed."""

    code = "robots_unavailable"
    http_status = 503


class TooLarge(FetchError):
    """Response body exceeded the configured size limit while streaming."""

    code = "too_large"
    http_status = 413


class UnsupportedContentType(FetchError):
    """Content-Type is not acceptable.

    Defined for completeness with SPEC §8; the M1 fetcher does not enforce it
    (deciding acceptable types belongs to the extract milestones). Reserved so
    no new contract is invented later.
    """

    code = "unsupported_content_type"
    http_status = 415


class ExtractionEmpty(FetchError):
    """Extraction produced no usable content (SPEC §8 extraction_empty=422).

    Raised by the PDF extractor (``app.extract.pdf``) when a document yields no
    extractable text at all — e.g. a scanned/image-only PDF. OCR is explicitly
    out of scope for M4, so such a document fails closed with a 422 rather than
    returning a 200 with empty content (CLAUDE.md / SPEC §4). The name mirrors
    the SPEC §8 ``extraction_empty`` code; it lives here with the rest of the
    typed errors so the M5 handler serializes it uniformly.
    """

    code = "extraction_empty"
    http_status = 422


class FetchFailed(FetchError):
    """Transport-level failure: DNS failure, connection reset, too many redirects."""

    code = "fetch_failed"
    http_status = 502


class Timeout(FetchError):
    """Connect or total timeout was exceeded."""

    code = "timeout"
    http_status = 504
