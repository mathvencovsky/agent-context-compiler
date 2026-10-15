"""Section answer key for M10a, from accepted-answer anchors (PREREG Amendment 6).

When an accepted answer links ``page#anchor`` on an official documentation
site, its author named the section that answers the question. For each ``dev``
task this module finds that element in the frozen page and takes the section's
text straight from the HTML, so the key depends on neither NINA's extraction nor
trafilatura:

* a section-like wrapper (``section``, ``div``, ``article``) carrying the id:
  all of it;
* a heading: its wrapper when the heading opens one, else the heading and what
  follows until the next heading of the same or a higher level;
* an API entry (``dt``, as in Sphinx): its ``dl``;
* an empty anchor (``<span id>``, ``<a name>``): the next element, or the parent;
* anything else: the element itself.

Each task's key is its first anchor, in link order, that resolves on one of the
task's URLs to a section of at most ``MAX_SECTION_TOKENS`` tokens with at least
one measurable unit (``bench.section_recall.units``). These rules were fixed
from the key's own sizes, before any system was measured. Every task that gets
no key is counted with the reason.

Usage (local files only; no network):
    uv run python -m bench.anchor_qrels
"""

from __future__ import annotations

import copy
import json
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import lxml.html

from app.index.tokens import count_tokens
from bench.corpus_store import CorpusStore
from bench.mine_dump import site_for_url
from bench.section_recall import units
from bench.snapshot import DEFAULT_CORPUS_DIR
from bench.tasks_schema import ExternalTask, load_external_tasks

_HEADINGS = ("h1", "h2", "h3", "h4", "h5", "h6")
_WRAPPERS = ("section", "div", "article")
_BREAKS = (
    tuple(
        "p pre blockquote li ul ol table tr hr div dt dd dl section article br".split()
    )
    + _HEADINGS
)
_CELLS = ("td", "th")
#: Larger "sections" are pages, not targets: the default ``assemble`` budget.
MAX_SECTION_TOKENS = 4_000


def anchor_links(answer_html: str) -> list[tuple[str, str]]:
    """``(page URL, anchor)`` of each allowlisted link with a fragment, in order.

    Page URLs are normalized like ``bench.mine_dump.cited_doc_urls`` (https,
    lowercase host, no fragment), so they match the frozen corpus keys.
    """
    if not answer_html.strip():
        return []
    root = lxml.html.fragment_fromstring(answer_html, create_parent="div")
    links: dict[tuple[str, str], None] = {}
    for href in root.xpath("//a/@href"):
        parts = urlsplit(href.strip())
        if parts.fragment and site_for_url(href):
            page = urlunsplit(
                ("https", parts.netloc.lower(), parts.path or "/", parts.query, "")
            )
            links[(page, parts.fragment)] = None
    return list(links)


def _tag(el: Any) -> str:
    return el.tag if isinstance(el.tag, str) else ""


def _section_elements(el: Any) -> list[Any]:
    """The elements that make up the section ``el`` anchors (module rules)."""
    tag = _tag(el)
    if tag in _WRAPPERS:
        return [el]
    if tag == "dt":
        parent = el.getparent()
        return [parent] if parent is not None and _tag(parent) == "dl" else [el]
    if tag in _HEADINGS:
        parent = el.getparent()
        if parent is not None and _tag(parent) in _WRAPPERS:
            if next(parent.iter(*_HEADINGS), None) is el:
                return [parent]
        elements = [el]
        for sibling in el.itersiblings():
            if not _tag(sibling):  # comments and processing instructions
                continue
            if _tag(sibling) in _HEADINGS and int(_tag(sibling)[1]) <= int(tag[1]):
                break
            elements.append(sibling)
        return elements
    if not (el.text_content() or "").strip():
        following = next((s for s in el.itersiblings() if _tag(s)), None)
        if following is not None:
            return _section_elements(following)
        parent = el.getparent()
        return _section_elements(parent) if parent is not None else []
    return [el]


def _text(elements: Sequence[Any]) -> str:
    """Plain text, one line per block element, table cells space-separated."""
    parts = []
    for original in elements:
        el = copy.deepcopy(original)
        el.tail = None
        for sub in el.iter():
            if _tag(sub) in _BREAKS:
                sub.tail = "\n" + (sub.tail or "")
            elif _tag(sub) in _CELLS:
                sub.tail = " " + (sub.tail or "")
        parts.append(el.text_content())
    lines = (line.strip() for line in "\n".join(parts).splitlines())
    return "\n".join(line for line in lines if line)


def section_text(html: str, anchor: str) -> str | None:
    """Text of the section ``anchor`` names in ``html``, or ``None`` if absent."""
    root = lxml.html.fromstring(html)
    found = root.xpath(
        "//*[@id=$a] | //a[@name=$a]", a=anchor
    )  # XPath variable: no quoting issues with odd anchors
    if not found:
        return None
    return _text(_section_elements(found[0]))


def task_key(
    task: ExternalTask, answer_html: str, store: CorpusStore
) -> dict[str, Any] | str:
    """The task's answer key, or the reason it has none."""
    links = anchor_links(answer_html)
    if not links:
        return "no_anchor_link"
    reason = "page_not_in_task_urls"
    for page, anchor in links:
        try:
            entry = store._entry(page)
        except KeyError:
            continue
        if entry.status != "ok" or entry.final_url not in task.urls:
            continue
        html = store.rendered_html(page) or store.raw_bytes(page).decode(
            "utf-8", errors="replace"
        )
        text = section_text(html, anchor)
        if not text:
            reason = "anchor_not_found"
            continue
        if count_tokens(text) > MAX_SECTION_TOKENS:
            reason = "section_too_broad"
            continue
        if not units(text):
            reason = "section_without_units"
            continue
        return {
            "task_id": task.id,
            "site": task.site,
            "url": entry.final_url,
            "anchor": anchor,
            "section": text,
            "section_tokens": count_tokens(text),
        }
    return reason


def main() -> None:  # pragma: no cover - reads local files
    store = CorpusStore(DEFAULT_CORPUS_DIR)
    tasks = [
        t
        for t in load_external_tasks(Path("bench/tasks/external.yaml"))
        if t.split == "dev"
    ]
    candidates_path = Path("bench/results/m10/candidates.jsonl")
    html_by_id = {
        f"so-{c['question_id']}": c["answer_html"]
        for c in map(
            json.loads, candidates_path.read_text(encoding="utf-8").splitlines()
        )
    }
    out = Path("bench/results/m10a/qrels.jsonl")
    out.parent.mkdir(parents=True, exist_ok=True)
    reasons: Counter[str] = Counter()
    keys = []
    for task in tasks:
        key = task_key(task, html_by_id[task.id], store)
        if isinstance(key, str):
            reasons[key] += 1
        else:
            keys.append(key)
    out.write_text(
        "".join(json.dumps(k, ensure_ascii=False) + "\n" for k in keys),
        encoding="utf-8",
    )
    sizes = sorted(k["section_tokens"] for k in keys)
    print(f"dev tasks {len(tasks)} | keys {len(keys)} | no key: {dict(reasons)}")
    if sizes:
        q = [sizes[int(p * (len(sizes) - 1))] for p in (0.1, 0.5, 0.9, 0.99)]
        print(f"section tokens p10/p50/p90/p99: {q}")
    print("keys by site:", dict(Counter(k["site"] for k in keys).most_common()))
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
