"""``POST /v1/sites/index``: a documentation site's ``llms.txt`` as links (J3).

An agent often knows the site but not which page answers its question. Many
documentation sites publish ``/llms.txt``, the author's own link list for
agents (llmstxt.org). This endpoint reads that ONE file and returns its links,
so the agent can choose the URLs to pass to ``assemble_context``. It is not a
crawler: it never follows a link, and it reads at most two candidate files.

Which file (fixed order, stops at the first usable one):

1. the URL itself, when its path ends in ``/llms.txt``; otherwise
2. ``<origin>/llms.txt``, then
3. ``<origin>/<first path segment>/llms.txt`` (Next.js publishes
   ``/docs/llms.txt``), only when step 2 found no usable file.

Each candidate goes through the same gates as a page: the URL-secret check,
robots.txt and the fetch guard on every redirect hop, with the usual body and
time limits (SPEC §3). A gate failure (blocked destination, robots, timeout)
is the usual error envelope. A missing file (404/410), an error status, an
HTML answer (sites that serve their app shell for any path) or a file without
links is a 200 with no entries and a warning saying which: never a silent empty
answer (CLAUDE.md). Results are not stored.

Every title, note and summary is the site's text: the structured fields carry
``trust: "untrusted_source"`` and ``context`` renders them inside NINA's
``<document>``/``<fragment>`` tags with the same neutralization as the concise
assemble format (:func:`app.retrieve.render.neutralize`).
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

from fastapi import APIRouter, Depends, Request

from app.api.auth import require_api_key
from app.api.documents import (
    ResolverDep,
    RobotsCheckerDep,
    SettingsDep,
    TransportDep,
)
from app.api.schemas import SiteIndexEntry, SiteIndexRequest, SiteIndexResponse
from app.api.usage import require_quota, set_usage_units
from app.config import Settings
from app.extract.llms_txt import LlmsEntry, LlmsIndex, parse_llms_txt
from app.fetch.client import FetchResult, Transport
from app.fetch.client import fetch as default_fetch
from app.fetch.errors import InvalidURL, TooLarge
from app.fetch.guard import Resolver, validate_url
from app.fetch.robots import RobotsChecker
from app.fetch.url_secrets import reject_secret_url
from app.index.tokens import count_tokens
from app.retrieve.render import NOTES_CLOSE, NOTES_OPEN, UNTRUSTED_LINE, neutralize
from app.retrieve.render import _attr as attr
from app.retrieve.render import _one_line as one_line

router = APIRouter(dependencies=[Depends(require_api_key), Depends(require_quota)])

#: Only this much of a large ``llms.txt`` is parsed (``llms_txt_too_large``).
MAX_INDEX_BYTES = 1024 * 1024
_ACCEPT = "text/markdown, text/plain;q=0.9, */*;q=0.1"
_TEXT_TYPES = frozenset({"text/plain", "text/markdown", "text/x-markdown", ""})
_MISSING = frozenset({404, 410})
#: A media type safe to echo in a warning (the header is the site's text).
_MEDIA_TYPE = re.compile(r"[a-z0-9.+-]{1,30}/[a-z0-9.+-]{1,40}")


def candidate_urls(url: str) -> list[str]:
    """The ``llms.txt`` URLs to try for ``url``, in order (module docstring)."""
    parts = urlsplit(url)
    if parts.path.endswith("/llms.txt"):
        return [urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))]
    root = urlunsplit((parts.scheme, parts.netloc, "/llms.txt", "", ""))
    segments = [s for s in parts.path.split("/") if s]
    if not segments or (len(segments) == 1 and "." in segments[0]):
        return [root]
    prefix = urlunsplit(
        (parts.scheme, parts.netloc, f"/{segments[0]}/llms.txt", "", "")
    )
    return [root, prefix]


def _warning(code: str, message: str, url: str) -> dict[str, object]:
    return {"code": code, "message": message, "url": url}


def _shown_type(content_type: str) -> str:
    if not content_type:
        return "none"
    return content_type if _MEDIA_TYPE.fullmatch(content_type) else "other"


def _looks_like_html(body: bytes) -> bool:
    head = body[:512].lstrip().lower()
    return head.startswith((b"<!doctype html", b"<html", b"<head", b"<body"))


def _read_candidate(
    url: str,
    *,
    robots: RobotsChecker,
    resolver: Resolver | None,
    transport: Transport | None,
    settings: Settings,
) -> tuple[str | None, dict[str, object] | None]:
    """``(text, None)`` for a usable file, else ``(None, warning)``."""
    robots.is_allowed(url)
    try:
        result: FetchResult = default_fetch(
            url,
            resolver=resolver,
            transport=transport,
            settings=settings,
            accept=_ACCEPT,
        )
    except TooLarge:
        return None, _warning(
            "llms_txt_too_large", f"{url} is larger than NINA's size limit", url
        )
    if result.status in _MISSING:
        return None, _warning("llms_txt_not_found", f"{url} does not exist (HTTP "
                              f"{result.status})", url)  # fmt: skip
    if not 200 <= result.status < 300:
        return None, _warning(
            "llms_txt_unavailable", f"{url} answered HTTP {result.status}", url
        )
    content_type = result.content_type.split(";")[0].strip().lower()
    if content_type not in _TEXT_TYPES or _looks_like_html(result.body):
        return None, _warning(
            "llms_txt_not_markdown",
            f"{url} is not a Markdown llms.txt (content type "
            f"{_shown_type(content_type)})",
            url,
        )
    body = result.body
    warning = None
    if len(body) > MAX_INDEX_BYTES:
        cut = body.rfind(b"\n", 0, MAX_INDEX_BYTES)
        body = body[: cut if cut > 0 else MAX_INDEX_BYTES]
        warning = _warning(
            "llms_txt_too_large",
            f"{url} is over {MAX_INDEX_BYTES // 1024} KB; only its start was read",
            url,
        )
    return body.decode("utf-8", errors="replace"), warning


def _entry_line(entry: LlmsEntry) -> str:
    title = one_line(entry.title).replace("[", "(").replace("]", ")")
    line = f"- [{title}]({one_line(entry.url)})"
    if entry.note:
        line += f": {one_line(entry.note)}"
    return neutralize(line)


def render_index(
    index: LlmsIndex | None,
    index_url: str | None,
    warnings: list[dict[str, object]],
    *,
    max_tokens: int,
) -> tuple[str, list[LlmsEntry]]:
    """The agent-facing text and the entries that fit ``max_tokens``.

    Entries are kept in file order until their lines would pass ``max_tokens``;
    the rest are counted in an ``index_truncated`` warning (appended to
    ``warnings``).
    """
    entries = index.entries if index is not None else []
    kept: list[LlmsEntry] = []
    body: list[str] = []
    if index is not None and (index.title or index.summary):
        header = [f"# {index.title}"] if index.title else []
        header += [f"> {index.summary}"] if index.summary else []
        body += ["<fragment>", *(neutralize(h) for h in header), "</fragment>"]
    spent = count_tokens("\n".join(body))
    section: str | None = None
    for entry in entries:
        line = _entry_line(entry)
        cost = count_tokens(line) + 1
        if entry.section != section:
            cost += count_tokens(entry.section) + 8
        if spent + cost > max_tokens:
            break
        if entry.section != section:
            if section is not None:
                body.append("</fragment>")
            section = entry.section
            opener = f' section="{attr(section)}"' if section else ""
            body.append(f"<fragment{opener}>")
        body.append(line)
        kept.append(entry)
        spent += cost
    if section is not None:
        body.append("</fragment>")
    if len(kept) < len(entries):
        warnings.append(
            {
                "code": "index_truncated",
                "message": f"{len(kept)} of {len(entries)} links shown "
                f"(max_tokens={max_tokens}); the rest are later in the file",
                "url": index_url or "",
            }
        )
    lines = [UNTRUSTED_LINE, f"site index: {len(kept)} of {len(entries)} links"]
    if index_url is not None and body:
        lines += [
            "",
            f'<document source="{attr(index_url)}" excerpt="site index (llms.txt)">',
            *body,
            "</document>",
        ]
    notes = [f"- warning {w['code']}: {w['message']}" for w in warnings if "code" in w]
    if notes:
        lines += [
            "",
            NOTES_OPEN,
            *(neutralize(one_line(n)) for n in notes),
            NOTES_CLOSE,
        ]
    return "\n".join(lines) + "\n", kept


def site_index(
    req: SiteIndexRequest,
    *,
    robots: RobotsChecker,
    resolver: Resolver | None,
    transport: Transport | None,
    settings: Settings,
) -> SiteIndexResponse:
    """Read the site's ``llms.txt`` for ``req.url`` (module docstring)."""
    reject_secret_url(req.url)
    guard = validate_url(req.url, resolver=resolver, settings=settings)
    if urlsplit(guard.normalized_url).scheme not in ("http", "https"):
        raise InvalidURL("only http(s) URLs have a site index")
    warnings: list[dict[str, object]] = []
    index: LlmsIndex | None = None
    index_url: str | None = None
    for candidate in candidate_urls(guard.normalized_url):
        text, warning = _read_candidate(
            candidate,
            robots=robots,
            resolver=resolver,
            transport=transport,
            settings=settings,
        )
        if warning is not None:
            warnings.append(warning)
        if text is not None:
            index_url = candidate
            index = parse_llms_txt(text, candidate)
            break
    if index is not None and index_url is not None:
        if index.invisible_removed:
            warnings.append(_warning("invisible_text_removed",
                                     f"{index.invisible_removed} invisible or control "
                                     "characters removed", index_url))  # fmt: skip
        if index.dropped:
            warnings.append(_warning("entries_dropped",
                                     f"{index.dropped} links with an unsafe or "
                                     "non-http URL left out", index_url))  # fmt: skip
        if not index.entries:
            warnings.append(_warning("llms_txt_empty",
                                     f"{index_url} has no Markdown link list",
                                     index_url))  # fmt: skip
    context, kept = render_index(index, index_url, warnings, max_tokens=req.max_tokens)
    return SiteIndexResponse(
        index_url=index_url,
        title=index.title if index else "",
        summary=index.summary if index else "",
        entries=[
            SiteIndexEntry(section=e.section, title=e.title, url=e.url, note=e.note)
            for e in kept
        ],
        entries_total=len(index.entries) if index else 0,
        context=context,
        warnings=warnings,
    )


@router.post("/v1/sites/index", response_model=SiteIndexResponse)
def post_site_index(
    req: SiteIndexRequest,
    request: Request,
    robots: RobotsCheckerDep,
    resolver: ResolverDep,
    transport: TransportDep,
    settings: SettingsDep,
) -> SiteIndexResponse:
    """The links of a documentation site's ``llms.txt`` (SPEC §8)."""
    set_usage_units(request, {"renders": 0})
    return site_index(
        req, robots=robots, resolver=resolver, transport=transport, settings=settings
    )
