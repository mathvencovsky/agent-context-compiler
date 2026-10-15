"""Mine M10 candidate tasks from the archived Stack Overflow dump (PREREG §2.1).

Source (PREREG Amendment 1): ``stackoverflow.com-Posts.7z`` and
``stackoverflow.com-Users.7z`` from the archive.org item ``stackexchange``. The
7z files are streamed through ``tar`` (libarchive's bsdtar, which ships with
Windows 10+; on Linux install ``libarchive-tools``), so nothing is extracted to
disk and no 7z dependency is added.

One pass over ``Posts.xml`` applies PREREG §2.1 inclusion rules 1-4 in order and
counts how many candidates each rule removes:

1. question created on or after 2023-01-01, with an accepted answer;
2. the accepted answer links an allowlisted official documentation page
   (PREREG Appendix C);
3. question score >= 1, accepted-answer score >= 1, question not closed;
4. no image in the question and a question body of at most 2,000 tokens.

Rows are sorted by Id, so a question always comes before its answers. Rule 5
(the cited page snapshots) runs later, when the pages are frozen. A second pass
over ``Users.xml`` fills in author names for attribution.

The output holds CC BY-SA content and stays out of git (``bench/results/`` is
gitignored).

Usage:
    uv run python -m bench.mine_dump --posts <dir>/stackoverflow.com-Posts.7z \
        --users <dir>/stackoverflow.com-Users.7z
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import IO, Any
from urllib.parse import urlsplit, urlunsplit

import lxml.html
from lxml import etree

from app.index.tokens import count_tokens

#: PREREG §2.1 rule 1: first day of the question window.
WINDOW_START = "2023-01-01"
#: PREREG §2.1 rule 4: longest question body kept, in cl100k tokens.
MAX_QUESTION_TOKENS = 2000

#: PREREG Appendix C: site key -> (host, path prefix) pairs.
ALLOWLIST: dict[str, tuple[tuple[str, str], ...]] = {
    "python": (("docs.python.org", "/"),),
    "sqlite": (("sqlite.org", "/"), ("www.sqlite.org", "/")),
    "postgresql": (("www.postgresql.org", "/docs/"),),
    "kubernetes": (("kubernetes.io", "/docs/"),),
    "flask": (("flask.palletsprojects.com", "/"),),
    "react": (("react.dev", "/"), ("legacy.reactjs.org", "/"), ("reactjs.org", "/")),
    "angular": (("angular.dev", "/"), ("angular.io", "/")),
    "mdn": (("developer.mozilla.org", "/"),),
    "rfc": (
        ("www.rfc-editor.org", "/rfc/"),
        ("datatracker.ietf.org", "/doc/html/rfc"),
        ("tools.ietf.org", "/html/rfc"),
    ),
    "docker": (("docs.docker.com", "/"),),
}

#: PREREG Appendix C ``version`` task-type pattern.
VERSION_RE = re.compile(
    r"\b(deprecat\w*|removed in|added in|new in|changed in|since version"
    r"|upgrad\w*|migrat\w*|breaking changes?)\b|\bversion\s+v?\d",
    re.IGNORECASE,
)

_BLOCK_TAGS = tuple(
    "p pre blockquote li ul ol table tr hr div h1 h2 h3 h4 h5 h6".split()
)


@dataclass
class MiningStats:
    """Counts for the PREREG report: how many candidates each rule removed."""

    questions_seen: int = 0
    rule1_kept: int = 0
    removed_rule2_no_doc_link: int = 0
    removed_rule3_score_or_closed: int = 0
    removed_rule4_image_or_length: int = 0
    accepted_answer_missing: int = 0
    kept: int = 0
    newest_post: str = ""


@dataclass
class _Question:
    """A rule-1 question waiting for its accepted answer."""

    id: int
    title: str
    created: str
    score: int
    closed: bool
    has_image: bool
    owner_id: int | None
    owner_name: str | None
    html: str | None = None  # kept only if the question passes rules 3-4
    text: str = ""
    tokens: int = 0


def _fragment(html: str) -> Any | None:
    """Parse a post body; ``None`` when it holds no content."""
    if not html.strip():
        return None
    try:
        return lxml.html.fragment_fromstring(html, create_parent="div")
    except etree.ParserError:  # e.g. a body that is only a comment
        return None


def html_to_text(html: str) -> str:
    """Plain text of a post body: block elements become lines, code keeps layout."""
    root = _fragment(html)
    if root is None:
        return ""
    for br in root.iter("br"):
        br.tail = "\n" + (br.tail or "")
    for el in root.iter(*_BLOCK_TAGS):
        el.text = "\n" + (el.text or "")
        el.tail = "\n" + (el.tail or "")
    lines = [line.rstrip() for line in root.text_content().splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def site_for_url(url: str) -> str | None:
    """The allowlisted site a link points to, or ``None``."""
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    host = parts.hostname.lower()
    path = parts.path or "/"
    for site, rules in ALLOWLIST.items():
        if any(host == h and path.startswith(prefix) for h, prefix in rules):
            return site
    return None


def cited_doc_urls(answer_html: str) -> list[tuple[str, str]]:
    """Allowlisted pages linked from an answer, as ``(site, url)`` in link order.

    Fragments are dropped and ``http`` becomes ``https``, so two links to
    sections of the same page count as one page.
    """
    root = _fragment(answer_html)
    if root is None:
        return []
    seen: dict[str, str] = {}
    for href in root.xpath("//a/@href"):
        site = site_for_url(href)
        if site is None:
            continue
        p = urlsplit(href.strip())
        url = urlunsplit(("https", p.netloc.lower(), p.path or "/", p.query, ""))
        seen.setdefault(url, site)
    return [(site, url) for url, site in seen.items()]


def blockquotes(answer_html: str) -> list[str]:
    """Whitespace-collapsed text of each blockquote: candidate gold evidence."""
    root = _fragment(answer_html) if "<blockquote" in answer_html else None
    if root is None:
        return []
    quotes = (" ".join(bq.text_content().split()) for bq in root.iter("blockquote"))
    return [q for q in quotes if q]


def task_type(
    n_pages: int, question_text: str, answer_text: str, answer_html: str
) -> str:
    """PREREG §2.1 task type; the first matching rule wins."""
    if n_pages >= 2:
        return "multi_doc"
    if VERSION_RE.search(question_text) or VERSION_RE.search(answer_text):
        return "version"
    if "<pre" in answer_html:
        return "code"
    return "fact"


def _int_or_none(value: str | None) -> int | None:
    return int(value) if value else None


def _question(row: Mapping[str, str]) -> _Question:
    q = _Question(
        id=int(row["Id"]),
        title=row.get("Title", ""),
        created=row.get("CreationDate", ""),
        score=int(row.get("Score", "0")),
        closed=bool(row.get("ClosedDate")),
        has_image="<img" in row.get("Body", "").lower(),
        owner_id=_int_or_none(row.get("OwnerUserId")),
        owner_name=row.get("OwnerDisplayName") or None,
    )
    # Bodies are only needed for questions that can still pass rules 3-4.
    if q.score >= 1 and not q.closed and not q.has_image:
        q.text = html_to_text(row.get("Body", ""))
        q.tokens = count_tokens(q.text)
        if q.tokens <= MAX_QUESTION_TOKENS:
            q.html = row.get("Body", "")
    return q


def mine_posts(
    rows: Iterable[Mapping[str, str]], stats: MiningStats
) -> Iterator[dict[str, Any]]:
    """Apply PREREG §2.1 rules 1-4 in order to dump rows; yield kept candidates."""
    pending: dict[int, _Question] = {}  # accepted answer id -> its question
    for row in rows:
        created = row.get("CreationDate", "")
        stats.newest_post = max(stats.newest_post, created)
        kind = row.get("PostTypeId")
        if kind == "1":
            stats.questions_seen += 1
        # Answers come after their questions, so nothing older can matter.
        if created < WINDOW_START:
            continue
        if kind == "1":
            accepted = row.get("AcceptedAnswerId")
            if accepted:
                stats.rule1_kept += 1
                pending[int(accepted)] = _question(row)
            continue
        if kind != "2":
            continue
        q = pending.pop(int(row["Id"]), None)
        if q is None:
            continue
        answer_html = row.get("Body", "")
        cited = cited_doc_urls(answer_html)
        if not cited:
            stats.removed_rule2_no_doc_link += 1
            continue
        if q.score < 1 or q.closed or int(row.get("Score", "0")) < 1:
            stats.removed_rule3_score_or_closed += 1
            continue
        if q.has_image or q.html is None:
            stats.removed_rule4_image_or_length += 1
            continue
        stats.kept += 1
        answer_text = html_to_text(answer_html)
        answer_id = int(row["Id"])
        yield {
            "question_id": q.id,
            "answer_id": answer_id,
            "site": cited[0][0],
            "cited_urls": [url for _, url in cited],
            "task_type": task_type(
                len(cited), f"{q.title}\n{q.text}", answer_text, answer_html
            ),
            "question_title": q.title,
            "question_html": q.html,
            "question_text": q.text,
            "question_tokens": q.tokens,
            "question_score": q.score,
            "question_created": q.created,
            "answer_html": answer_html,
            "answer_text": answer_text,
            "answer_score": int(row.get("Score", "0")),
            "answer_blockquotes": blockquotes(answer_html),
            "question_owner_id": q.owner_id,
            "question_owner_name": q.owner_name,
            "answer_owner_id": _int_or_none(row.get("OwnerUserId")),
            "answer_owner_name": row.get("OwnerDisplayName") or None,
            "question_url": f"https://stackoverflow.com/q/{q.id}",
            "answer_url": f"https://stackoverflow.com/a/{answer_id}",
            "license": "CC BY-SA 4.0",
        }
    stats.accepted_answer_missing = len(pending)


def user_names(rows: Iterable[Mapping[str, str]], ids: set[int]) -> dict[int, str]:
    """Display names of the users in ``ids`` (Users.xml rows)."""
    names: dict[int, str] = {}
    for row in rows:
        user_id = int(row["Id"])
        if user_id in ids:
            names[user_id] = row.get("DisplayName", "")
    return names


def iter_rows(stream: IO[bytes]) -> Iterator[Mapping[str, str]]:
    """Attributes of each ``<row>`` in a dump XML stream, in constant memory.

    Each mapping is only valid until the next one is requested.
    """
    for _event, elem in etree.iterparse(
        stream, events=("end",), tag="row", huge_tree=True
    ):
        yield elem.attrib
        elem.clear()
        while elem.getprevious() is not None:
            del elem.getparent()[0]


@contextmanager
def _member_stream(tar: str, archive: str, member: str) -> Iterator[IO[bytes]]:
    """Stream ``member`` out of a 7z ``archive`` through bsdtar."""
    proc = subprocess.Popen([tar, "-xOf", archive, member], stdout=subprocess.PIPE)
    assert proc.stdout is not None
    try:
        yield proc.stdout
    finally:
        proc.stdout.close()
        if proc.wait() != 0:
            raise RuntimeError(f"{tar} failed reading {member} from {archive}")


def _default_tar() -> str:
    windows_tar = Path(os.environ.get("WINDIR", "C:/Windows")) / "System32" / "tar.exe"
    return str(windows_tar) if windows_tar.exists() else "bsdtar"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--posts", required=True, help="stackoverflow.com-Posts.7z")
    parser.add_argument("--users", required=True, help="stackoverflow.com-Users.7z")
    parser.add_argument("--out", default="bench/results/m10/candidates.jsonl")
    parser.add_argument("--tar", default=_default_tar(), help="bsdtar executable")
    args = parser.parse_args(argv)

    stats = MiningStats()
    with _member_stream(args.tar, args.posts, "Posts.xml") as stream:
        candidates = list(mine_posts(iter_rows(stream), stats))

    ids = {
        c[key]
        for c in candidates
        for key in ("question_owner_id", "answer_owner_id")
        if c[key] is not None
    }
    with _member_stream(args.tar, args.users, "Users.xml") as stream:
        names = user_names(iter_rows(stream), ids)
    for c in candidates:
        for role in ("question", "answer"):
            owner = c[f"{role}_owner_id"]
            if owner is not None and owner in names:
                c[f"{role}_owner_name"] = names[owner]

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for c in candidates:
            fh.write(json.dumps(c, ensure_ascii=False) + "\n")
    stats_path = out.with_name("mining_stats.json")
    stats_path.write_text(json.dumps(asdict(stats), indent=2), encoding="utf-8")
    print(json.dumps(asdict(stats), indent=2))
    print(f"wrote {len(candidates)} candidates to {out}")


if __name__ == "__main__":
    main()
