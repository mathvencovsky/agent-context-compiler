"""Pydantic request/response schemas for the resolve/get endpoints — M5 (SPEC §8).

These models define the public HTTP contract of ``POST /v1/documents/resolve`` and
``GET /v1/documents/{document_id}``. The IR models (``SourceInfo``, ``Metadata``,
``Block``, ``Warning``) are reused from :mod:`app.extract.ir` so the structured
payload the pipeline produces is surfaced verbatim — no second contract.

``markdown`` and ``blocks`` are optional on the wire: the handler fills them only
for the formats the request asked for (``formats``). ``trust`` is a fixed
``"untrusted_source"`` constant (SPEC §8 — every resolved document is untrusted
content). ``examples`` are attached so the generated OpenAPI shows the SPEC §8
request/response shapes.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.extract.ir import Block, Metadata, SourceInfo, Warning

ResolveFormat = Literal["markdown", "blocks"]
RenderDecision = Literal["auto", "never", "always"]


class ResolveRequest(BaseModel):
    """Body of ``POST /v1/documents/resolve`` (SPEC §8)."""

    url: str = Field(examples=["https://docs.example.com/guide"])
    formats: list[ResolveFormat] = Field(default=["markdown", "blocks"])
    force_refresh: bool = False
    render: RenderDecision = "auto"

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "url": "https://docs.example.com/guide",
                    "formats": ["markdown", "blocks"],
                    "force_refresh": False,
                    "render": "auto",
                }
            ]
        }
    }


class Stats(BaseModel):
    """Counters returned alongside a resolved document (SPEC §8)."""

    blocks: int
    tokens_markdown: int
    tokens_raw_html: int
    cache_hit: bool


class ResolveResponse(BaseModel):
    """Response envelope of resolve/get-document (SPEC §8)."""

    document_id: str
    version_id: str
    source: SourceInfo
    metadata: Metadata
    markdown: str | None = None
    blocks: list[Block] | None = None
    stats: Stats
    warnings: list[Warning]
    trust: Literal["untrusted_source"] = "untrusted_source"

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "document_id": "doc_0123456789abcdef",
                    "version_id": "ver_fedcba9876543210",
                    "source": {
                        "url": "https://docs.example.com/guide",
                        "final_url": "https://docs.example.com/guide",
                        "content_type": "text/html",
                        "fetched_at": "2026-10-11T00:00:00Z",
                        "etag": '"abc123"',
                        "last_modified": "Wed, 11 Oct 2026 00:00:00 GMT",
                        "content_hash": "e3b0c44298fc1c149afbf4c8996fb924",
                        "render_mode": "http",
                    },
                    "metadata": {
                        "title": "Guide",
                        "lang": "en",
                        "published_at": None,
                        "modified_at": None,
                        "jsonld": [],
                    },
                    "markdown": "# Guide\n\nText.",
                    "blocks": [],
                    "stats": {
                        "blocks": 1,
                        "tokens_markdown": 4,
                        "tokens_raw_html": 12,
                        "cache_hit": False,
                    },
                    "warnings": [],
                    "trust": "untrusted_source",
                }
            ]
        }
    }


class ErrorDetail(BaseModel):
    """The ``error`` object of the SPEC §8 error envelope."""

    code: str
    message: str
    details: dict[str, object]


class ErrorBody(BaseModel):
    """SPEC §8 error envelope: ``{"error": {code, message, details}}``."""

    error: ErrorDetail

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "error": {
                        "code": "blocked_destination",
                        "message": "destination IP is not a global unicast address",
                        "details": {},
                    }
                }
            ]
        }
    }
