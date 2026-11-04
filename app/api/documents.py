"""Resolve and get-document endpoints with the SPEC §5/§8 cache state machine — M5.

``POST /v1/documents/resolve`` is the first public endpoint. It validates the URL
through the SSRF guard, serves from storage on a cache hit, and otherwise fetches
(through the audited synchronous M1 client), extracts (M3 HTML / M4 PDF), persists
raw + IR blobs and a new version row, and returns the SPEC §8 envelope.

Handlers are plain ``def`` (NOT ``async def``): FastAPI runs them on a threadpool,
so the audited SYNCHRONOUS fetch/render/SQLAlchemy stack is reused as-is without
rewriting it into async (DECISIONS.md 2026-10-11 — the architecture decision made
by the human). ``httpx`` is never imported here; the only outbound path is the
guarded ``app.fetch.client.fetch`` and the transport seam is typed via the
``app.fetch.client.Transport`` alias.

Every dependency the integration tests need to override is exposed as a FastAPI
``Depends`` seam: the session factory, the :class:`BlobStore`, the
:class:`RobotsChecker`, the guard ``resolver``, the httpx ``transport`` and the
``now`` clock. Tests swap them via ``app.dependency_overrides``.

Conditional revalidation (DECISIONS.md 2026-10-29, superseding the 2026-10-11
adaptation): once the current version is past its TTL, resolve sends its stored
``ETag`` / ``Last-Modified`` back as ``If-None-Match`` / ``If-Modified-Since``
(the client accepts exactly these two validators). A ``304 Not Modified``
touches ``fetched_at`` and serves the stored version, exactly what the full GET
did for unchanged bytes (same-hash dedup), without downloading or hashing the
body. A 304 is trusted only for the URL the validators came from: when the
redirect chain now ends elsewhere, the page is fetched again without them.
``force_refresh`` always does a full GET. Anything other than a 304 follows the
full-GET path, including the ``content_hash`` dedup.

Extractor upgrades (DECISIONS.md 2026-10-29): a version is one (raw bytes,
extractor version). When the stored current version was built by another
``EXTRACTOR_VERSION`` (NULL included), every path that would serve it (cache
hit, 304, same-hash dedup) re-extracts the raw bytes instead, under a new
``version_id``; the old version row, blobs and chunks stay as they were. On a
cache hit or a 304 the STORED raw bytes are re-extracted (no refetch of the
page). The pipeline is re-run exactly as for a fresh fetch, so a page whose
HTTP body needs a browser render (``render_mode == "browser"``) is rendered
again: the rendered DOM is never stored, only the HTTP body is. That render is
network access, so it is preceded by the robots gate and charged to the render
quota like any render.
"""

from __future__ import annotations

import functools
import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session, sessionmaker

from app.api.alternate import FetchAlternate, use_markdown_alternate
from app.api.auth import require_api_key
from app.api.errors import NotFound
from app.api.schemas import (
    RenderDecision,
    ResolveFormat,
    ResolveRequest,
    ResolveResponse,
    Stats,
)
from app.api.usage import charge_render, require_quota, set_usage_units
from app.config import Settings, get_settings
from app.extract.html import extract_html
from app.extract.ir import (
    Document,
    SourceInfo,
    Warning,
    current_extractor_version,
    make_document_id,
    make_version_id,
)
from app.extract.markdown import (
    MARKDOWN_CONTENT_TYPES,
    PLAIN_TEXT_CONTENT_TYPE,
    extract_markdown,
    extract_plain_text,
    is_markdown_url,
)
from app.extract.pdf import extract_pdf
from app.extract.to_markdown import to_markdown
from app.fetch.client import FetchResult, Transport
from app.fetch.client import fetch as default_fetch
from app.fetch.detect import (
    CONTENT_TYPE_MISMATCH_WARNING_CODE,
    detect_charset,
    looks_like_pdf,
    parse_content_type,
    should_render,
)
from app.fetch.errors import UnsupportedContentType, UpstreamStatus, refusal_note
from app.fetch.guard import GuardResult, Resolver, validate_url
from app.fetch.render import RENDERED_WITH_BROWSER_WARNING_CODE, render_url
from app.fetch.robots import RobotsChecker
from app.fetch.url_secrets import reject_secret_url
from app.index.tokens import count_tokens
from app.store.blobs import BlobStore, shared_blob_store
from app.store.models import DocumentVersion, Source
from app.store.repo import (
    DocumentRepo,
    domain_from_url,
    make_session_factory,
    shared_engine,
)

# Bearer auth gates every /v1 route (no-op in dev; enforced otherwise — SPEC §8).
# require_quota spends one token from the per-key bucket before the handler runs
# (M9). /health is registered on the app in main.py and is intentionally NOT
# gated.
router = APIRouter(dependencies=[Depends(require_api_key), Depends(require_quota)])

_HTML_CONTENT_TYPES = frozenset(
    {"text/html", "application/xhtml+xml", "application/xml", "text/xml"}
)
_PDF_CONTENT_TYPE = "application/pdf"
#: Accept header sent only when settings.fetch_accept_markdown is on (SPEC §3.5).
_ACCEPT_MARKDOWN_FIRST = (
    "text/markdown, text/html;q=0.9, application/xhtml+xml;q=0.9, "
    "application/pdf;q=0.9, text/plain;q=0.8, */*;q=0.5"
)
_EMPTY_CONTENT_WARNING_CODE = "empty_content"


# --------------------------------------------------------------------------- #
# Dependency seams (overridable in tests via app.dependency_overrides)
# --------------------------------------------------------------------------- #
def provide_settings() -> Settings:
    """Return the process settings (overridable in tests)."""
    return get_settings()


def provide_session_factory() -> sessionmaker[Session]:
    """A session factory on the process-wide engine (one connection pool)."""
    return make_session_factory(shared_engine(get_settings()))


def provide_blob_store() -> BlobStore:
    """The process-wide S3 blob store (overridable in tests)."""
    return shared_blob_store()


@functools.cache
def provide_robots_checker() -> RobotsChecker:
    """The process-wide robots.txt gate (overridable in tests).

    One instance per process, so its per-origin TTL cache (SPEC §3.2, 24 h)
    survives across requests; a checker per request threw it away and
    refetched robots.txt on every cache miss (research round 4, K L2).
    """
    return RobotsChecker()


def provide_resolver() -> Resolver | None:
    """Return the guard resolver (None -> the guard's default SocketResolver)."""
    return None


def provide_transport() -> Transport | None:
    """Return the httpx transport seam (None -> real network via the guard)."""
    return None


def provide_now() -> datetime:
    """Return the current UTC time (overridable in tests)."""
    return datetime.now(UTC)


SessionFactoryDep = Annotated[sessionmaker[Session], Depends(provide_session_factory)]
BlobStoreDep = Annotated[BlobStore, Depends(provide_blob_store)]
RobotsCheckerDep = Annotated[RobotsChecker, Depends(provide_robots_checker)]
ResolverDep = Annotated[Resolver | None, Depends(provide_resolver)]
TransportDep = Annotated[Transport | None, Depends(provide_transport)]
NowDep = Annotated[datetime, Depends(provide_now)]
SettingsDep = Annotated[Settings, Depends(provide_settings)]
VersionIdQuery = Annotated[str | None, Query()]
FormatsQuery = Annotated[list[ResolveFormat] | None, Query()]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _warnings_as_payload(warnings: list[Warning]) -> list[dict[str, object]]:
    """Serialize IR warnings for JSONB storage."""
    return [w.model_dump() for w in warnings]


def _build_response(
    doc: Document,
    *,
    formats: list[ResolveFormat],
    cache_hit: bool,
    tokens_raw_html: int,
) -> ResolveResponse:
    """Assemble the SPEC §8 envelope, including only the requested formats."""
    markdown = to_markdown(doc)
    include_markdown = "markdown" in formats
    include_blocks = "blocks" in formats

    stats = Stats(
        blocks=len(doc.blocks),
        tokens_markdown=count_tokens(markdown),
        tokens_raw_html=tokens_raw_html,
        cache_hit=cache_hit,
    )
    return ResolveResponse(
        document_id=doc.document_id,
        version_id=doc.version_id,
        source=doc.source,
        metadata=doc.metadata,
        markdown=markdown if include_markdown else None,
        blocks=doc.blocks if include_blocks else None,
        stats=stats,
        warnings=doc.warnings,
    )


def _extract_document(
    *,
    body: bytes,
    content_type: str,
    content_type_header: str | None,
    guard: GuardResult,
    final_url: str,
    etag: str | None,
    last_modified: str | None,
    content_hash: str,
    fetched_at: datetime,
    render: RenderDecision,
    resolver: Resolver | None,
    settings: Settings,
    on_render: Callable[[], None] | None = None,
    link_header: str | None = None,
    fetch_alternate: FetchAlternate | None = None,
) -> tuple[Document, str]:
    """Extract an IR document from a fetched body (HTML, PDF, Markdown, text).

    Returns the Document and the decoded raw text used for token counting.
    Raises :class:`UnsupportedContentType` for anything that is not HTML, PDF,
    Markdown or plain text (SPEC §4). A body that starts with the ``%PDF-``
    signature is extracted as a PDF whatever its declared type, with a
    ``content_type_mismatch`` warning. ``fetch_alternate`` (set only when
    ``settings.markdown_alternate`` is on) lets an HTML page be replaced by its
    same-origin Markdown alternate (SPEC §3.5, ``app/api/alternate.py``).
    ``on_render`` (M9) is invoked right BEFORE a headless-browser render so the
    caller can charge the per-key daily render quota (and reject with 429 before
    the browser work happens).
    """
    mismatch: str | None = None
    if content_type != _PDF_CONTENT_TYPE and looks_like_pdf(body):
        # The bytes are a PDF whatever the label says (octet-stream, x-download,
        # no type...): extract it as a PDF and say so (G L2).
        mismatch = content_type or "(none)"

    if content_type == _PDF_CONTENT_TYPE or mismatch is not None:
        source = SourceInfo(
            url=guard.normalized_url,
            final_url=final_url,
            content_type=content_type,
            fetched_at=fetched_at,
            etag=etag,
            last_modified=last_modified,
            content_hash=content_hash,
            render_mode="pdf",
        )
        doc = extract_pdf(body, source, settings=settings)
        if mismatch is not None:
            doc.warnings.append(
                Warning(
                    code=CONTENT_TYPE_MISMATCH_WARNING_CODE,
                    message=(
                        f"the server declared content-type {mismatch!r} but the "
                        "body is a PDF; it was extracted as a PDF"
                    ),
                    block_id=None,
                )
            )
        # PDF raw text for tokens: use the extracted block text (bytes are binary).
        raw_text = "\n".join(b.text for b in doc.blocks)
        return doc, raw_text

    if (
        content_type in MARKDOWN_CONTENT_TYPES
        or content_type == PLAIN_TEXT_CONTENT_TYPE
    ):
        _media, charset = parse_content_type(content_type_header)
        text = body.decode(
            detect_charset(content_type_charset=charset, body=body), errors="replace"
        )
        text_source = SourceInfo(
            url=guard.normalized_url,
            final_url=final_url,
            content_type=content_type,
            fetched_at=fetched_at,
            etag=etag,
            last_modified=last_modified,
            content_hash=content_hash,
            render_mode="http",
        )
        as_markdown = content_type in MARKDOWN_CONTENT_TYPES or is_markdown_url(
            final_url
        )
        extract = extract_markdown if as_markdown else extract_plain_text
        return extract(text, text_source), text

    if content_type not in _HTML_CONTENT_TYPES:
        raise UnsupportedContentType(
            f"content-type {content_type!r} is not HTML, PDF, Markdown or text",
            details={"content_type": content_type, "url": guard.normalized_url},
        )

    _media, charset = parse_content_type(content_type_header)
    decoded = body.decode(
        detect_charset(content_type_charset=charset, body=body), errors="replace"
    )

    http_source = SourceInfo(
        url=guard.normalized_url,
        final_url=final_url,
        content_type=content_type,
        fetched_at=fetched_at,
        etag=etag,
        last_modified=last_modified,
        content_hash=content_hash,
        render_mode="http",
    )
    doc = extract_html(decoded, http_source)

    # (SPEC §3.5) The page's same-origin Markdown alternate, when enabled.
    alternate_warning: Warning | None = None
    if fetch_alternate is not None:
        alternate_doc, alternate_warning = use_markdown_alternate(
            html_text=decoded,
            link_header=link_header,
            html_doc=doc,
            fetch_alternate=fetch_alternate,
            settings=settings,
        )
        if alternate_doc is not None:
            return alternate_doc, decoded
        if alternate_warning is not None:
            doc.warnings.append(alternate_warning)

    main_text_len = sum(len(b.text) for b in doc.blocks if b.type != "code")

    if should_render(
        main_text_len=main_text_len,
        html_text=decoded,
        render=render,
        settings=settings,
    ):
        if on_render is not None:
            on_render()
        rendered = render_url(
            guard.normalized_url, resolver=resolver, settings=settings
        )
        browser_source = SourceInfo(
            url=guard.normalized_url,
            final_url=rendered.final_url,
            content_type=content_type,
            fetched_at=fetched_at,
            etag=etag,
            last_modified=last_modified,
            content_hash=content_hash,
            render_mode="browser",
        )
        doc = extract_html(rendered.html, browser_source)
        if alternate_warning is not None:
            doc.warnings.append(alternate_warning)
        doc.warnings.append(
            Warning(
                code=RENDERED_WITH_BROWSER_WARNING_CODE,
                message="document was rendered with a headless browser",
                block_id=None,
            )
        )
        decoded = rendered.html

    return doc, decoded


def _raw_bytes_tokens(body: bytes, *, known: tuple[str, int] | None = None) -> int:
    """cl100k tokens of ``body`` decoded as UTF-8 (errors replaced).

    This is what a response served from a stored version reports as
    ``stats.tokens_raw_html``. ``known`` is a (text, count) pair the caller
    already has; when the text is that same string, its count is reused.
    """
    text = body.decode("utf-8", errors="replace")
    if known is not None and known[0] == text:
        return known[1]
    return count_tokens(text)


def _stored_tokens_raw_html(version: DocumentVersion, blobs: BlobStore) -> int:
    """``stats.tokens_raw_html`` of a stored version, without the raw blob.

    Stored at creation since migration 0007; older rows (NULL) still download
    and count the raw body, which gives the same number.
    """
    if version.tokens_raw_html is not None:
        return version.tokens_raw_html
    return _raw_bytes_tokens(blobs.get_raw(version.id))


def _render_charger(request: Request | None) -> Callable[[], None] | None:
    """Return an ``on_render`` callback charging the render quota, or None (M9)."""
    if request is None:
        return None
    return lambda: charge_render(request)


def _alternate_fetcher(
    *,
    robots: RobotsChecker,
    resolver: Resolver | None,
    transport: Transport | None,
    settings: Settings,
) -> FetchAlternate | None:
    """Fetch a Markdown alternate like any page (robots, then the guarded
    client), or ``None`` when ``settings.markdown_alternate`` is off."""
    if not settings.markdown_alternate:
        return None

    def fetch_alternate(url: str) -> FetchResult:
        reject_secret_url(url)
        robots.is_allowed(url)
        return default_fetch(
            url, resolver=resolver, transport=transport, settings=settings
        )

    return fetch_alternate


def _ensure_not_empty(doc: Document) -> None:
    """Never return a 200 with empty content and no warning (CLAUDE.md)."""
    if not doc.blocks and not doc.warnings:
        doc.warnings.append(
            Warning(
                code=_EMPTY_CONTENT_WARNING_CODE,
                message="extraction produced no content blocks",
                block_id=None,
            )
        )


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #
@router.post("/v1/documents/resolve", response_model=ResolveResponse)
def resolve(
    req: ResolveRequest,
    request: Request,
    session_factory: SessionFactoryDep,
    blobs: BlobStoreDep,
    robots: RobotsCheckerDep,
    resolver: ResolverDep,
    transport: TransportDep,
    now: NowDep,
    settings: SettingsDep,
) -> ResolveResponse:
    """Resolve a URL to its agent-ready document (HTTP entry point).

    Thin wrapper over :func:`resolve_document` that passes the live
    :class:`Request` so the M9 usage units / render-quota hooks fire. The
    assemble orchestrator calls :func:`resolve_document` directly (no request).
    """
    return resolve_document(
        req,
        session_factory=session_factory,
        blobs=blobs,
        robots=robots,
        resolver=resolver,
        transport=transport,
        now=now,
        settings=settings,
        request=request,
    )


def resolve_document(
    req: ResolveRequest,
    *,
    session_factory: sessionmaker[Session],
    blobs: BlobStore,
    robots: RobotsChecker,
    resolver: Resolver | None,
    transport: Transport | None,
    now: datetime,
    settings: Settings,
    request: Request | None = None,
) -> ResolveResponse:
    """Resolve a URL to its agent-ready document, using the cache state machine.

    ``request`` is present on the HTTP path (``None`` when the assemble
    orchestrator calls this internally); the M9 usage units / render-quota hooks
    only fire when a request is present.
    """
    # Default usage units (M9): no render unless the fetch path renders below.
    if request is not None:
        set_usage_units(request, {"renders": 0})
    # Before the guard, the cache and any network access (exfiltration via the
    # request itself; also covers every assemble URL and the MCP tools).
    reject_secret_url(req.url)
    guard = validate_url(req.url, resolver=resolver, settings=settings)
    normalized_url = guard.normalized_url
    document_id = make_document_id(normalized_url)
    domain = domain_from_url(normalized_url)

    session = session_factory()
    try:
        repo = DocumentRepo(session)
        source = repo.get_or_create_source(normalized_url, domain, document_id)
        current = repo.get_current_version(source)
        ctx = _ResolveContext(
            req=req,
            request=request,
            repo=repo,
            source=source,
            blobs=blobs,
            robots=robots,
            resolver=resolver,
            transport=transport,
            settings=settings,
            guard=guard,
            document_id=document_id,
        )
        extractor_version = current_extractor_version()

        # (3) CACHE HIT: fresh version, no force_refresh -> serve from storage.
        if (
            current is not None
            and not req.force_refresh
            and _is_fresh(current, now, settings.resolve_cache_ttl_seconds)
        ):
            if current.extractor_version != extractor_version:
                # (3b) Fresh bytes, outdated extraction: re-extract the stored
                # raw body (no page fetch) into a new version.
                response = _reextract_stored(
                    current,
                    fetched_at=_aware(current.fetched_at),
                    cache_hit=True,
                    ctx=ctx,
                )
                session.commit()
                return response
            doc = blobs.get_ir(current.id)
            response = _build_response(
                doc,
                formats=req.formats,
                cache_hit=True,
                tokens_raw_html=_stored_tokens_raw_html(current, blobs),
            )
            session.commit()
            return response

        # (4) robots gate before any network fetch.
        robots.is_allowed(normalized_url)

        # (5) GET through the audited client, conditional when a stored
        # version has validators (module docstring / DECISIONS.md).
        def fetch_page(validators: DocumentVersion | None) -> FetchResult:
            return default_fetch(
                normalized_url,
                resolver=resolver,
                transport=transport,
                settings=settings,
                accept=(
                    _ACCEPT_MARKDOWN_FIRST if settings.fetch_accept_markdown else None
                ),
                if_none_match=validators.etag if validators else None,
                if_modified_since=validators.last_modified if validators else None,
            )

        revalidating = (
            current
            if current is not None
            and not req.force_refresh
            and (current.etag or current.last_modified)
            else None
        )
        result = fetch_page(revalidating)
        stored_doc: Document | None = None
        if result.status == 304 and revalidating is not None:
            stored_doc = blobs.get_ir(revalidating.id)
            if stored_doc.source.final_url != result.final_url:
                # The validators belong to another URL than the one that
                # answered 304 (the redirect chain changed): do not trust it.
                result = fetch_page(None)

        # (6) 304 Not Modified: refresh fetched_at only, no new version.
        if result.status == 304 and current is not None:
            repo.touch_fetched_at(current, now)
            if current.extractor_version != extractor_version:
                # Unchanged bytes, outdated extraction: re-extract the stored body.
                response = _reextract_stored(
                    current, fetched_at=now, cache_hit=False, ctx=ctx
                )
                session.commit()
                return response
            doc = stored_doc if stored_doc is not None else blobs.get_ir(current.id)
            response = _build_response(
                doc,
                formats=req.formats,
                cache_hit=False,
                tokens_raw_html=_stored_tokens_raw_html(current, blobs),
            )
            session.commit()
            return response

        # (6b) Only a 2xx answer is a document: an error page (404, 500, a 3xx
        # without Location, a 304 with nothing cached) is never extracted.
        if not 200 <= result.status < 300:
            raise UpstreamStatus(
                f"{result.final_url}: {refusal_note(result.status)}"
                if refusal_note(result.status)
                else f"{result.final_url} answered HTTP {result.status}",
                details={"url": result.final_url, "upstream_status": result.status},
            )

        # (7) content hash + dedup.
        content_hash = hashlib.sha256(result.body).hexdigest()
        if (
            current is not None
            and current.content_hash == content_hash
            and current.extractor_version == extractor_version
        ):
            repo.touch_fetched_at(current, now)
            doc = blobs.get_ir(current.id)
            response = _build_response(
                doc,
                formats=req.formats,
                cache_hit=False,
                tokens_raw_html=_stored_tokens_raw_html(current, blobs),
            )
            session.commit()
            return response

        etag = result.headers.get("etag")
        last_modified = result.headers.get("last-modified")

        # (8) extract by content-type.
        doc, raw_text = _extract_document(
            body=result.body,
            content_type=result.content_type,
            content_type_header=result.headers.get("content-type"),
            guard=guard,
            final_url=result.final_url,
            etag=etag,
            last_modified=last_modified,
            content_hash=content_hash,
            fetched_at=now,
            render=req.render,
            resolver=resolver,
            settings=settings,
            on_render=_render_charger(request),
            link_header=result.headers.get("link"),
            fetch_alternate=_alternate_fetcher(
                robots=robots, resolver=resolver, transport=transport, settings=settings
            ),
        )

        # (9)-(11) persist and respond.
        response = _store_version(
            doc,
            body=result.body,
            raw_text=raw_text,
            content_type_header=result.headers.get("content-type"),
            link_header=result.headers.get("link"),
            cache_hit=False,
            ctx=ctx,
        )
        session.commit()
        return response
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@dataclass(frozen=True)
class _ResolveContext:
    """What the persist / re-extract helpers of one resolve call share."""

    req: ResolveRequest
    request: Request | None
    repo: DocumentRepo
    source: Source
    blobs: BlobStore
    robots: RobotsChecker
    resolver: Resolver | None
    transport: Transport | None
    settings: Settings
    guard: GuardResult
    document_id: str


def _store_version(
    doc: Document,
    *,
    body: bytes,
    raw_text: str,
    content_type_header: str | None,
    link_header: str | None,
    cache_hit: bool,
    ctx: _ResolveContext,
) -> ResolveResponse:
    """Persist ``doc`` as the source's current version and build the response.

    Steps (9)-(11) of the state machine. The version id is
    ``make_version_id(document_id, content_hash, EXTRACTOR_VERSION)``; when a
    row with that id already exists (the page went back to bytes it served
    before, A -> B -> A, or a concurrent resolve stored it first), that row is
    made current again and served instead of inserting a duplicate.
    """
    # (M9) record whether a browser render happened for usage accounting.
    if ctx.request is not None:
        set_usage_units(
            ctx.request,
            {"renders": 1 if doc.source.render_mode == "browser" else 0},
        )

    # (9) explicit failure: never a 200 empty with no warning.
    _ensure_not_empty(doc)

    # (10) persist raw + IR blobs and the new version row.
    extractor_version = current_extractor_version()
    content_hash = doc.source.content_hash
    version_id = make_version_id(ctx.document_id, content_hash, extractor_version)
    existing = ctx.repo.get_version(version_id)
    if existing is not None:
        ctx.repo.set_current_version(ctx.source, existing)
        if doc.source.fetched_at > _aware(existing.fetched_at):
            ctx.repo.touch_fetched_at(existing, doc.source.fetched_at)
        return _build_response(
            ctx.blobs.get_ir(existing.id),
            formats=ctx.req.formats,
            cache_hit=cache_hit,
            tokens_raw_html=count_tokens(raw_text),
        )
    doc.version_id = version_id
    response_tokens = count_tokens(raw_text)
    raw_key = ctx.blobs.put_raw(version_id, body)
    ir_key = ctx.blobs.put_ir(version_id, doc)
    ctx.repo.create_version(
        source=ctx.source,
        version_id=version_id,
        content_hash=content_hash,
        fetched_at=doc.source.fetched_at,
        etag=doc.source.etag,
        last_modified=doc.source.last_modified,
        render_mode=doc.source.render_mode,
        raw_blob_key=raw_key,
        ir_blob_key=ir_key,
        warnings=_warnings_as_payload(doc.warnings),
        extractor_version=extractor_version,
        content_type_header=content_type_header,
        link_header=link_header,
        tokens_raw_html=_raw_bytes_tokens(body, known=(raw_text, response_tokens)),
    )

    # (11) build the response (markdown/blocks per req.formats).
    return _build_response(
        doc,
        formats=ctx.req.formats,
        cache_hit=cache_hit,
        tokens_raw_html=response_tokens,
    )


def _reextract_stored(
    stored: DocumentVersion,
    *,
    fetched_at: datetime,
    cache_hit: bool,
    ctx: _ResolveContext,
) -> ResolveResponse:
    """Re-extract a stored version's raw bytes with the current extractor.

    Used when ``stored.extractor_version`` is not ``EXTRACTOR_VERSION`` and the
    bytes are known to be current (fresh cache, or a 304). The page is not
    fetched again: the raw body, its response headers and the fetch metadata
    come from storage (the old IR's ``source`` for the content type and final
    URL). Rows written before migration 0006 have no stored Content-Type
    header, so their charset is sniffed from the body (BOM, ``<meta>``, else
    UTF-8). A browser render or a Markdown alternate, if the pipeline asks for
    one, is network access: the render is preceded by the robots gate (and the
    render quota), the alternate goes through robots and the guarded client
    like any alternate. The stored version itself is never modified.
    """
    body = ctx.blobs.get_raw(stored.id)
    old_source = ctx.blobs.get_ir(stored.id).source
    content_type_header = stored.content_type_header or old_source.content_type
    charge = _render_charger(ctx.request)

    def on_render() -> None:
        ctx.robots.is_allowed(ctx.guard.normalized_url)
        if charge is not None:
            charge()

    doc, raw_text = _extract_document(
        body=body,
        content_type=old_source.content_type,
        content_type_header=content_type_header,
        guard=ctx.guard,
        final_url=old_source.final_url,
        etag=stored.etag,
        last_modified=stored.last_modified,
        content_hash=stored.content_hash,
        fetched_at=fetched_at,
        render=ctx.req.render,
        resolver=ctx.resolver,
        settings=ctx.settings,
        on_render=on_render,
        link_header=stored.link_header,
        fetch_alternate=_alternate_fetcher(
            robots=ctx.robots,
            resolver=ctx.resolver,
            transport=ctx.transport,
            settings=ctx.settings,
        ),
    )
    return _store_version(
        doc,
        body=body,
        raw_text=raw_text,
        content_type_header=stored.content_type_header,
        link_header=stored.link_header,
        cache_hit=cache_hit,
        ctx=ctx,
    )


def _aware(value: datetime) -> datetime:
    """``value`` as an aware UTC datetime (the DB column is naive)."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


@router.get("/v1/documents/{document_id}", response_model=ResolveResponse)
def get_document(
    document_id: str,
    session_factory: SessionFactoryDep,
    blobs: BlobStoreDep,
    version_id: VersionIdQuery = None,
    formats: FormatsQuery = None,
) -> ResolveResponse:
    """Return a stored document from the storage layer WITHOUT any refetch."""
    default_formats: list[ResolveFormat] = ["markdown", "blocks"]
    wanted = formats if formats else default_formats
    session = session_factory()
    try:
        repo = DocumentRepo(session)
        version = _resolve_version(repo, document_id, version_id)
        doc = blobs.get_ir(version.id)
        return _build_response(
            doc,
            formats=wanted,
            cache_hit=True,
            tokens_raw_html=_stored_tokens_raw_html(version, blobs),
        )
    finally:
        session.close()


def _resolve_version(
    repo: DocumentRepo, document_id: str, version_id: str | None
) -> DocumentVersion:
    """Resolve the requested version, or raise :class:`NotFound`."""
    if version_id is not None:
        version = repo.get_version(version_id)
        if version is None or version.source_id != document_id:
            raise NotFound(
                "requested version does not exist for this document",
                details={"document_id": document_id, "version_id": version_id},
            )
        return version

    source = repo.get_source_by_document_id(document_id)
    if source is None:
        raise NotFound("document not found", details={"document_id": document_id})
    current = repo.get_current_version(source)
    if current is None:
        raise NotFound(
            "document has no stored version", details={"document_id": document_id}
        )
    return current


def _is_fresh(version: DocumentVersion, now: datetime, ttl_seconds: int) -> bool:
    """True if the version was fetched within the TTL window."""
    fetched_at = version.fetched_at
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=UTC)
    age = (now - fetched_at).total_seconds()
    return age < ttl_seconds
