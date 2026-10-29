"""Parse an ``llms.txt`` file into a list of links (research J3; llmstxt.org).

An ``llms.txt`` is the site author's own map for agents: an H1 title, an
optional ``>`` blockquote summary, free text, then ``##`` sections holding
Markdown link lists (``- [Name](url): notes``). NINA reads it as a map, not as
a page, so an agent can pick which documentation URLs to pass to
``assemble_context``. One file is read; nothing is followed (not a crawler).

Everything in the file is untrusted text. The parser keeps only what a link
list needs and makes each field harmless before it reaches an agent:

* a link becomes an entry only when its URL, resolved against the file's own
  URL, is an absolute ``http``/``https`` URL of at most 2,048 characters with a
  host, no user info, no whitespace or control characters, and no credential
  that :func:`app.fetch.url_secrets.find_secret` recognizes; anything else is
  dropped and counted;
* titles, notes, the H1 and the summary lose control and invisible characters
  (:func:`app.extract.invisible.strip_invisible`), collapse whitespace into one
  line and are cut at :data:`MAX_TITLE_CHARS` / :data:`MAX_NOTE_CHARS`.

Rendering escapes NINA's own tags (``app/api/sites.py``). Pure and
deterministic: no I/O, no LLM.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

from app.extract.invisible import strip_invisible
from app.fetch.url_secrets import find_secret

__all__ = ["LlmsEntry", "LlmsIndex", "parse_llms_txt"]

MAX_TITLE_CHARS = 120
MAX_NOTE_CHARS = 200
MAX_URL_CHARS = 2048

#: No lazy group before a trailing ``\s*#*``: that form is quadratic on a long
#: line of spaces. Closing ``#`` marks are stripped in code instead.
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_QUOTE = re.compile(r"^>\s?(.*)$")
#: ``- [Title](url "optional title"): note`` (also ``*``/``+`` bullets).
_LINK_ITEM = re.compile(
    r"^\s*[-*+]\s+\[(?P<title>[^\]]*)\]\((?P<url><[^>]*>|[^)\s]+)"
    r"(?:\s+\"[^\"]*\")?\)\s*(?:[:–—-]\s*(?P<note>.*))?$"
)
_SPACE = re.compile(r"\s+")
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f  ]")


@dataclass(frozen=True)
class LlmsEntry:
    """One link of the index: its section, title, URL and the author's note."""

    section: str
    title: str
    url: str
    note: str


@dataclass
class LlmsIndex:
    """The parsed file: title, summary, entries in file order, and counts."""

    title: str = ""
    summary: str = ""
    entries: list[LlmsEntry] = field(default_factory=list)
    #: Links whose URL failed the safety rules above.
    dropped: int = 0
    #: Invisible or control characters removed from the file.
    invisible_removed: int = 0


def _clean(text: str, limit: int) -> str:
    """One line of plain text, at most ``limit`` characters."""
    one_line = _SPACE.sub(" ", _CONTROL.sub(" ", text)).strip()
    if len(one_line) <= limit:
        return one_line
    return one_line[: limit - 1].rstrip() + "…"


def _safe_url(raw: str, base_url: str) -> str | None:
    """The absolute URL for ``raw``, or ``None`` when it is not safe to show."""
    raw = raw.strip("<>").strip()
    if not raw or _CONTROL.search(raw) or any(c.isspace() for c in raw):
        return None
    url = urljoin(base_url, raw)
    if len(url) > MAX_URL_CHARS:
        return None
    try:
        parts = urlsplit(url)
        parts.port  # noqa: B018 - raises ValueError on a malformed port
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    if "@" in parts.netloc or find_secret(url) is not None:
        return None
    return url


def parse_llms_txt(text: str, base_url: str) -> LlmsIndex:
    """Parse ``text`` (an ``llms.txt`` served at ``base_url``)."""
    clean, removed = strip_invisible(text.lstrip("﻿"))
    index = LlmsIndex(invisible_removed=removed)
    section = ""
    summary: list[str] = []
    in_code = False
    for line in clean.splitlines():
        stripped = line.strip()
        if stripped.startswith(("```", "~~~")):
            in_code = not in_code
            continue
        if in_code:
            continue
        heading = _HEADING.match(stripped)
        if heading:
            level = len(heading.group(1))
            name = heading.group(2).rstrip().rstrip("#").rstrip()
            if level == 1 and not index.title and not index.entries:
                index.title = _clean(name, MAX_TITLE_CHARS)
            else:
                section = _clean(name, MAX_TITLE_CHARS)
            continue
        quote = _QUOTE.match(stripped)
        if quote and not index.entries and not section:
            summary.append(quote.group(1))
            continue
        item = _LINK_ITEM.match(line)
        if item is None:
            continue
        url = _safe_url(item.group("url"), base_url)
        if url is None:
            index.dropped += 1
            continue
        index.entries.append(
            LlmsEntry(
                section=section,
                title=_clean(item.group("title"), MAX_TITLE_CHARS) or url,
                url=url,
                note=_clean(item.group("note") or "", MAX_NOTE_CHARS),
            )
        )
    index.summary = _clean(" ".join(summary), MAX_NOTE_CHARS * 2)
    return index
