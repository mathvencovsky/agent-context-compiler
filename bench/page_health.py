"""Pages that answer 200 but carry no documentation (no cost).

A site can answer 200 with a page that is not the documentation: an empty
shell, a "page not found" page served with 200 (a soft 404), a bot challenge
(Cloudflare's "Just a moment...", a CAPTCHA) or a cookie-consent wall. Status
codes miss all four; the text gives them away when it is short and opens with
the marker. ``classify`` names the case, or returns ``None`` for a plausible
page.

PREREG Amendment 16 uses it twice:

1. **Snapshot pages.** A task with a cited page whose frozen snapshot is one of
   these is excluded from every arm before any session (``unhealthy_tasks``):
   no arm could find the answer in it, and the live arm could, which would
   bias the comparison against the corpus arms.
2. **Live fetches.** ``blocked_sessions`` reads the transcripts and lists the
   sessions where a fetch tool got a challenge, a soft 404 or a fetch error.
   The primary analysis keeps them (a blocked fetch is what that arm does in
   the real world); the report gives the rate per arm and a sensitivity
   analysis without those tasks.

Usage (needs the corpus; reports the snapshot exclusions)::

    uv run python -m bench.page_health [--splits pilot,holdout]
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

#: Below this many characters a page is too short to be a documentation page.
EMPTY_CHARS = 200
#: Markers count only on pages shorter than this: a long page that mentions
#: "404 Not Found" (an HTTP reference, say) is documentation.
SHORT_CHARS = 2_000
#: A soft-404 marker must appear this early (title or first heading).
HEAD_CHARS = 300

_CHALLENGE = re.compile(
    r"just a moment\.\.\.|checking your browser|verify (?:that )?you are (?:a )?human"
    r"|attention required!?\s*\|\s*cloudflare|enable javascript and cookies to continue"
    r"|ddos protection by|complete the security check|are you a robot|captcha"
    r"|access denied|request blocked|cf-chl",
    re.IGNORECASE,
)
_SOFT_404 = re.compile(
    r"\b404\b|page not found|not found|could not be found|cannot be found"
    r"|does(?: not|n't) exist|no longer exists|page you (?:are|were) looking for",
    re.IGNORECASE,
)
_CONSENT = re.compile(
    r"we use cookies|accept all cookies|cookie (?:settings|preferences|policy)"
    r"|manage (?:your )?consent|your privacy choices",
    re.IGNORECASE,
)

#: The tools that fetch a page in some arm.
FETCH_TOOLS = ("fetch_page", "WebFetch", "resolve_url", "get_document")


def classify(text: str) -> str | None:
    """``"empty"``, ``"challenge"``, ``"soft_404"``, ``"consent_wall"`` or ``None``."""
    stripped = text.strip()
    if len(stripped) < EMPTY_CHARS:
        return "empty"
    if len(stripped) >= SHORT_CHARS:
        return None
    if _CHALLENGE.search(stripped):
        return "challenge"
    if _SOFT_404.search(stripped[:HEAD_CHARS]):
        return "soft_404"
    if _CONSENT.search(stripped):
        return "consent_wall"
    return None


def unhealthy_tasks(
    tasks: Iterable[tuple[str, list[str]]], markdown_of: Callable[[str], str]
) -> dict[str, dict[str, str]]:
    """Tasks, as ``(id, cited_urls)``, with an unhealthy cited page.

    Maps each excluded id to ``{url: case}`` for its unhealthy pages.
    """
    cache: dict[str, str | None] = {}
    out: dict[str, dict[str, str]] = {}
    for task_id, cited in tasks:
        for url in cited:
            if url not in cache:
                cache[url] = classify(markdown_of(url))
            case = cache[url]
            if case is not None:
                out.setdefault(task_id, {})[url] = case
    return out


@dataclass
class ArmFetches:
    """Fetch-tool results of one arm, by outcome."""

    results: int = 0
    cases: Counter[str] = field(default_factory=Counter)
    sessions: set[str] = field(default_factory=set)


def _text(content: object) -> str:
    if isinstance(content, list):
        return "".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
    return content if isinstance(content, str) else ""


def session_fetch_cases(lines: Iterable[str]) -> tuple[int, Counter[str]]:
    """Fetch-tool results in one transcript and their unhealthy cases.

    A live fetch tool's text is often a summary of the page, so only the
    challenge and soft-404 markers count there; a failed call is
    ``fetch_error``.
    """
    names: dict[str, str] = {}
    n = 0
    cases: Counter[str] = Counter()
    for line in lines:
        if not line.strip():
            continue
        msg = json.loads(line)
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if msg.get("type") == "AssistantMessage" and "name" in block:
                names[str(block.get("id"))] = str(block["name"])
            elif msg.get("type") == "UserMessage" and "tool_use_id" in block:
                name = names.get(str(block["tool_use_id"]), "")
                if name.rsplit("__", 1)[-1] not in FETCH_TOOLS:
                    continue
                n += 1
                text = _text(block.get("content"))
                if block.get("is_error"):
                    cases["fetch_error"] += 1
                    continue
                case = classify(text)
                if case in ("challenge", "soft_404"):
                    cases[case] += 1
    return n, cases


def blocked_sessions(transcripts: Path) -> dict[str, ArmFetches]:
    """Per arm, fetch results by case and the ``task__arm`` pairs with any case.

    Reads ``<task>__<arm>__<attempt>.jsonl``; only the last attempt of a pair
    counts, since earlier ones were retried as infrastructure failures.
    """
    last: dict[tuple[str, str], tuple[int, Path]] = {}
    for path in transcripts.glob("*.jsonl"):
        task, arm, attempt = path.stem.rsplit("__", 2)
        key = (task, arm)
        if key not in last or int(attempt) > last[key][0]:
            last[key] = (int(attempt), path)
    arms: dict[str, ArmFetches] = defaultdict(ArmFetches)
    for (task, arm), (_, path) in sorted(last.items()):
        with path.open(encoding="utf-8") as fh:
            n, cases = session_fetch_cases(fh)
        entry = arms[arm]
        entry.results += n
        entry.cases.update(cases)
        if cases:
            entry.sessions.add(f"{task}__{arm}")
    return dict(arms)


def report_blocked(arms: dict[str, ArmFetches]) -> str:
    lines = ["| arm | fetch results | challenge | soft_404 | fetch_error | sessions |"]
    lines.append("|---|---|---|---|---|---|")
    for arm, e in sorted(arms.items()):
        lines.append(
            f"| {arm} | {e.results} | {e.cases['challenge']} | {e.cases['soft_404']} "
            f"| {e.cases['fetch_error']} | {len(e.sessions)} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> None:  # pragma: no cover - corpus
    from bench.corpus_store import CorpusStore
    from bench.page_length import page_markdown
    from bench.snapshot import DEFAULT_CORPUS_DIR
    from bench.tasks_schema import load_external_tasks

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--splits", default="pilot,holdout")
    parser.add_argument(
        "--transcripts", type=Path, help="also report live fetches in these transcripts"
    )
    args = parser.parse_args(argv)
    splits = set(args.splits.split(","))
    store = CorpusStore(DEFAULT_CORPUS_DIR)

    def markdown_of(url: str) -> str:
        try:
            return page_markdown(store, url)
        except (KeyError, ValueError):
            return ""

    tasks = [
        t for t in load_external_tasks("bench/tasks/external.yaml") if t.split in splits
    ]
    bad = unhealthy_tasks(((t.id, t.cited_urls) for t in tasks), markdown_of)
    cases = Counter(c for pages in bad.values() for c in pages.values())
    print(
        f"tasks: {len(tasks)}; with an unhealthy cited snapshot: {len(bad)}",
        dict(cases),
    )
    for task_id, pages in sorted(bad.items()):
        for url, case in pages.items():
            print(f"  {task_id}: {case} {url}")
    if args.transcripts:
        print(report_blocked(blocked_sessions(args.transcripts)), end="")


if __name__ == "__main__":
    main()
