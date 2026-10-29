"""Use a page's same-origin Markdown alternate in resolve (SPEC §3.5, M10b).

Only runs when ``settings.markdown_alternate`` is on. Given the HTML page that
was fetched (and already extracted), :func:`use_markdown_alternate`:

1. reads the declared alternate (``Link`` header or ``<link rel="alternate"
   type="text/markdown">``) and keeps it only if it is on the page's origin;
2. fetches it with the caller's ``fetch_alternate`` (robots + the guarded
   client: every hop through ``app/fetch/guard.py``, same body cap and
   timeouts as any fetch);
3. requires a 2xx answer, a Markdown or plain-text media type, a final URL
   still on the page's origin, and blocks after extraction (the same invisible
   Unicode stripping applies);
4. requires the alternate's text to agree with the page's visible text
   (``markdown_alternate_min_overlap``), so content shown only to agents is
   not swapped in for what people read.

On success it returns the Markdown document (``source.alternate_url`` and
``source.alternate_content_hash`` set, ``markdown_alternate_used`` warning).
On any failure it returns ``None`` and a ``markdown_alternate_failed``
warning, and resolve keeps the HTML extraction. The requested URL, the HTML
final URL, its content hash and the stored raw body stay those of the HTML
page: the alternate never changes which resource the caller asked for.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable

from app.config import Settings
from app.extract.alternate import (
    find_markdown_alternate,
    same_origin,
    text_agreement,
    visible_text,
)
from app.extract.ir import Document, Warning
from app.extract.markdown import (
    MARKDOWN_CONTENT_TYPES,
    PLAIN_TEXT_CONTENT_TYPE,
    extract_markdown,
)
from app.fetch.client import FetchResult
from app.fetch.detect import detect_charset, parse_content_type
from app.fetch.errors import FetchError

MARKDOWN_ALTERNATE_USED_WARNING_CODE = "markdown_alternate_used"
MARKDOWN_ALTERNATE_FAILED_WARNING_CODE = "markdown_alternate_failed"

FetchAlternate = Callable[[str], FetchResult]


def _failed(url: str, reason: str) -> tuple[None, Warning]:
    return None, Warning(
        code=MARKDOWN_ALTERNATE_FAILED_WARNING_CODE,
        message=(
            f"the page declares a Markdown alternate ({url}) but it was not "
            f"used: {reason}; extracted from the HTML instead"
        ),
        block_id=None,
    )


def use_markdown_alternate(
    *,
    html_text: str,
    link_header: str | None,
    html_doc: Document,
    fetch_alternate: FetchAlternate,
    settings: Settings,
) -> tuple[Document | None, Warning | None]:
    """The Markdown alternate's document, or ``None`` and why (see module doc).

    Returns ``(None, None)`` when the page declares no usable same-origin
    alternate (nothing was attempted, nothing to report). The page's metadata
    (title, language, dates, JSON-LD) is kept from the HTML extraction.
    """
    http_source = html_doc.source
    page_url = http_source.final_url
    alt_url = find_markdown_alternate(html_text, page_url, link_header)
    if alt_url is None:
        return None, None

    try:
        result = fetch_alternate(alt_url)
    except FetchError as exc:
        return _failed(alt_url, f"fetch failed ({exc.code})")
    if not 200 <= result.status < 300:
        return _failed(alt_url, f"it answered HTTP {result.status}")
    if not same_origin(result.final_url, page_url):
        return _failed(alt_url, "it redirected to another origin")
    if (
        result.content_type not in MARKDOWN_CONTENT_TYPES
        and result.content_type != PLAIN_TEXT_CONTENT_TYPE
    ):
        return _failed(alt_url, f"content-type {result.content_type!r}")

    _media, charset = parse_content_type(result.headers.get("content-type"))
    text = result.body.decode(
        detect_charset(content_type_charset=charset, body=result.body),
        errors="replace",
    )
    alt_hash = hashlib.sha256(result.body).hexdigest()
    parse_source = http_source.model_copy(update={"final_url": result.final_url})
    doc = extract_markdown(text, parse_source)
    if not doc.blocks:
        return _failed(alt_url, "it is empty")

    agreement = text_agreement(
        "\n".join(b.text for b in doc.blocks), visible_text(html_text)
    )
    if agreement < settings.markdown_alternate_min_overlap:
        return _failed(
            alt_url,
            f"only {agreement:.2f} of its text appears on the HTML page "
            f"(minimum {settings.markdown_alternate_min_overlap:.2f})",
        )

    md_title = doc.metadata.title
    doc.metadata = html_doc.metadata.model_copy(deep=True)
    doc.metadata.title = doc.metadata.title or md_title
    doc.source = http_source.model_copy(
        update={"alternate_url": result.final_url, "alternate_content_hash": alt_hash}
    )
    doc.warnings.append(
        Warning(
            code=MARKDOWN_ALTERNATE_USED_WARNING_CODE,
            message=(
                f"extracted from the page's same-origin Markdown alternate "
                f"({result.final_url}) instead of the HTML; "
                f"{agreement:.2f} of its text appears on the HTML page"
            ),
            block_id=None,
        )
    )
    return doc, None
