"""Actionable hints about an assemble result (SPEC §8, ``hints``).

Hints are NINA's own metadata, kept apart from page content: they never quote
page text (only the caller's URL and NINA's status codes) and they describe the
situation and the options instead of telling the agent what to do. Public
issues against documentation tools show both failure modes this avoids: silent
empty results and injected "instructions" mixed into tool output
(docs/CONCORRENTES.md).

Pure and deterministic: no I/O, no LLM.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from app.retrieve.assemble import AssembleResult

#: Upper bound of ``max_tokens`` (SPEC §8).
_MAX_TOKENS = 32_000

_SOURCE_OPTIONS = {
    "robots_disallowed": "the site's robots.txt forbids reading it, so NINA does "
    "not read this page; the option is another source",
    "robots_unavailable": "the site's robots.txt could not be read, so NINA does "
    "not read the page; the options are retrying later or another source",
    "blocked_destination": "the address points to a private or reserved network "
    "and is never fetched",
    "invalid_url": "the URL is not a valid public http(s) address",
    "too_large": "the page is larger than NINA's size limit; the option is a more "
    "specific page",
    "unsupported_content_type": "the content type is not HTML, PDF, Markdown or "
    "plain text",
    "extraction_empty": "the page has no extractable text",
    "upstream_status": "the site answered with an HTTP error instead of the page "
    "(404: the URL is wrong or moved; 5xx: the options are retrying later or "
    "another URL)",
    "timeout": "the site did not answer in time; the options are retrying later "
    "or another URL",
    "fetch_failed": "the site refused or failed the request; the options are "
    "retrying later or another URL",
}


#: A path segment that names a documentation release: ``3.12``, ``v1.30``,
#: ``16``, ``2.3.x``, or a moving label such as ``stable``.
_VERSION_SEGMENT = re.compile(
    r"^(v?\d+(\.\d+)*(\.x)?|current|stable|latest|dev|devel|main|master|nightly)$",
    re.IGNORECASE,
)


def version_label(url: str) -> tuple[str, str] | None:
    """``(host, label)`` of the first version-like path segment, if any."""
    parts = urlsplit(url)
    for segment in parts.path.split("/"):
        if _VERSION_SEGMENT.match(segment):
            return parts.netloc.lower(), segment.lower()
    return None


def _mixed_versions(result: AssembleResult) -> list[dict[str, object]]:
    """One hint per site whose delivered fragments span several releases.

    Fragments of different releases can contradict each other, the "context
    clash" failure mode (docs/CONTEXT_ENGINEERING.md, P7).
    """
    labels: dict[str, set[str]] = {}
    for frag in result.fragments:
        found = version_label(frag.source_url)
        if found is not None:
            labels.setdefault(found[0], set()).add(found[1])
    hints: list[dict[str, object]] = []
    for host in sorted(labels):
        versions = sorted(labels[host])
        if len(versions) < 2:
            continue
        hints.append(
            {
                "code": "mixed_versions",
                "message": f"The fragments come from different versions of the "
                f"{host} documentation ({', '.join(versions)}) and may disagree. "
                "Option: the URLs of one version only.",
                "details": {"host": host, "versions": versions},
            }
        )
    return hints


def build_hints(result: AssembleResult) -> list[dict[str, object]]:
    """The hints for ``result``: failed sources, low coverage, budget, versions."""
    hints: list[dict[str, object]] = []
    for src in result.sources:
        if src.status == "ok":
            continue
        reason = _SOURCE_OPTIONS.get(src.status, "the page could not be read")
        hints.append(
            {
                "code": "source_failed",
                "message": f"{src.url} was not used: {reason}.",
                "details": {"url": src.url, "status": src.status},
            }
        )
    if result.coverage == "insufficient":
        hints.append(
            {
                "code": "low_coverage",
                "message": "The highest relevance found is low: these URLs may not "
                "contain the answer. Options: other URLs or a more specific query.",
                "details": {},
            }
        )
    if result.budget_limited:
        suggested = min(2 * result.budget_tokens, _MAX_TOKENS)
        hints.append(
            {
                "code": "budget_limited",
                "message": "The budget ran out before other relevant fragments. "
                f"Option: max_tokens={suggested}.",
                "details": {"max_tokens": suggested},
            }
        )
    hints += _mixed_versions(result)
    return hints
