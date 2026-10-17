"""NINA Python SDK — a typed, synchronous client for the NINA API (M9).

Install the ``nina-client`` distribution and talk to a running NINA API::

    from nina_client import NinaClient

    client = NinaClient("https://nina.example.com", "nina_...")
    pkg = client.assemble("Which header authenticates?", ["https://docs/auth"])
    for fragment in pkg.fragments:
        print(fragment.source_url, fragment.md)

Errors come back as the typed exceptions in :mod:`nina_client.errors` (e.g.
:class:`UnauthorizedError`, :class:`RateLimitedError`), mapping the SPEC §8 error
table. See the repo ``docs/QUICKSTART.md`` for runnable examples.
"""

from __future__ import annotations

from nina_client.client import NinaClient
from nina_client.errors import (
    AblationNotAllowedError,
    AllSourcesFailedError,
    BlockedDestinationError,
    ExtractionEmptyError,
    FetchFailedError,
    InvalidURLError,
    NinaError,
    NotFoundError,
    RateLimitedError,
    RobotsDisallowedError,
    RobotsUnavailableError,
    TimeoutError,
    TooLargeError,
    UnauthorizedError,
    UnsupportedContentTypeError,
    UpstreamStatusError,
)
from nina_client.models import (
    AssembleResponse,
    ConciseAssembleResponse,
    EvidenceItem,
    Fragment,
    FragmentScores,
    ResolveResponse,
    SourceInfo,
    SourceStatus,
    Stats,
    TokenUsage,
)

__all__ = [
    "NinaClient",
    # Errors (SPEC §8)
    "NinaError",
    "InvalidURLError",
    "BlockedDestinationError",
    "RobotsDisallowedError",
    "RobotsUnavailableError",
    "TooLargeError",
    "UnsupportedContentTypeError",
    "ExtractionEmptyError",
    "FetchFailedError",
    "UpstreamStatusError",
    "TimeoutError",
    "NotFoundError",
    "AllSourcesFailedError",
    "AblationNotAllowedError",
    "UnauthorizedError",
    "RateLimitedError",
    # Models
    "ResolveResponse",
    "AssembleResponse",
    "ConciseAssembleResponse",
    "SourceInfo",
    "Stats",
    "Fragment",
    "FragmentScores",
    "EvidenceItem",
    "SourceStatus",
    "TokenUsage",
]
