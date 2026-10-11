"""Draft benchmark tasks with the answerer LLM (SPEC §10.2).

``draft`` seeds ``tasks.yaml`` with AI-authored *drafts* a human later reviews
(``bench/review_tasks.py``). Quality matters: a draft whose quote is page chrome
or whose question is trivial wastes the human's validation time, so this
generator works from the CLEAN intermediate representation (the M3/M4
``extract_html``/``extract_pdf``), never the raw HTML, and asks the model for a
specific-detail question with a verbatim quote.

For each ``ok`` corpus entry it:

* builds the IR with the real extractor, so blocks are clean content (no CSS,
  nav or ``<title>`` boilerplate);
* picks several *quotable* blocks — paragraphs, tables, code and lists with
  enough substance to carry a specific detail (a parameter, default, limit,
  header, version, status code, …);
* asks the injected answerer :class:`LLMProvider` to turn each block into one
  ``{"question", "answer", "quote"}`` JSON object, and keeps the draft only when
  the returned ``quote`` is found LITERALLY in the block text (SPEC §10.4);
* assigns a canonical category (SPEC §10.2) from IR signals (a parameter table →
  ``params_table``, a changelog URL/title → ``changelog``) falling back to the
  site's rich labels;
* marks every draft ``author="ai_draft"``, ``validated=False``;
* makes roughly 10% unanswerable (``expected=["INSUFFICIENT"]``) by asking a
  question whose answer is deliberately absent from the block.

Nothing here touches the network: the provider is injected (the deterministic
fake in tests, the real OpenAI answerer in the human run) and all text comes
from the frozen :class:`~bench.corpus_store.CorpusStore`.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence

from app.extract.html import extract_html
from app.extract.ir import Block, Document, SourceInfo
from app.extract.pdf import extract_pdf
from app.fetch.detect import parse_content_type
from app.providers.llm.base import LLMProvider
from bench.corpus_store import CorpusStore, ManifestEntry
from bench.tasks_schema import Category, Evidence, Task

#: Deterministic map from a rich ``sites.yaml`` label to a canonical SPEC §10.2
#: task category. Labels absent here fall back by the rules in
#: :func:`rich_label_to_category`.
_RICH_LABEL_MAP: dict[str, Category] = {
    "api_reference": "api_reference",
    "http_reference": "api_reference",
    "sql_reference": "api_reference",
    "cli_reference": "api_reference",
    "language_reference": "api_reference",
    "configuration_reference": "params_table",
    "build_reference": "params_table",
    "changelog": "changelog",
    "release_notes": "changelog",
    "pdf": "pdf",
    "standards": "pdf",
    "protocol_specification": "pdf",
    "guide": "guide",
    "tutorial": "guide",
    "troubleshooting": "guide",
    "storage": "guide",
    "spa_js": "guide",
}

#: Draft at most this many tasks per corpus entry (several quotable blocks each).
_TASKS_PER_ENTRY = 3

#: One in this many drafts is made unanswerable (``expected=["INSUFFICIENT"]``).
_INSUFFICIENT_EVERY = 10

#: A quotable block must carry at least this much text to host a specific detail.
_MIN_BLOCK_CHARS = 60
#: …and no more than this, so the quote stays a focused literal substring.
_MAX_BLOCK_CHARS = 1500

#: Signals (in URL or title) that a page is a changelog/release page.
_CHANGELOG_RE = re.compile(
    r"(change\s*log|release[-\s]?notes|whatsnew|what's new|releases|releaselog)",
    re.IGNORECASE,
)

_WS_RE = re.compile(r"\s+")


def rich_label_to_category(labels: Sequence[str]) -> Category:
    """Map a site's rich labels to one canonical SPEC §10.2 category.

    The first label with a known mapping wins (``_RICH_LABEL_MAP``). If none is
    known the function falls back to ``"guide"`` — the most generic bucket — so
    the mapping is total and deterministic for any label list.
    """
    for label in labels:
        mapped = _RICH_LABEL_MAP.get(label)
        if mapped is not None:
            return mapped
    return "guide"


def _category_for_entry(entry: ManifestEntry, doc: Document, block: Block) -> Category:
    """Choose a canonical category from IR signals, then the site's rich labels.

    PDFs are always ``pdf``. A changelog signal in the URL or document title wins
    next; a parameter/data table block maps to ``params_table``. Otherwise the
    site's rich labels decide (``rich_label_to_category``).
    """
    if entry.content_type == "application/pdf":
        return "pdf"
    haystack = f"{entry.url} {doc.metadata.title or ''}"
    if _CHANGELOG_RE.search(haystack):
        return "changelog"
    if block.type == "table":
        return "params_table"
    return rich_label_to_category(entry.rich_categories)


def _document_for(store: CorpusStore, entry: ManifestEntry) -> Document:
    """Build the clean IR for a corpus entry using the real M3/M4 extractor."""
    raw = store.raw_bytes(entry.url)
    source = SourceInfo(
        url=entry.url,
        final_url=entry.final_url,
        content_type=entry.content_type,
        fetched_at=_parse_dt(entry.fetched_at),
        etag=None,
        last_modified=None,
        content_hash=entry.content_hash,
        render_mode="pdf" if entry.content_type == "application/pdf" else "http",
    )
    if entry.content_type == "application/pdf":
        return extract_pdf(raw, source)

    rendered = store.rendered_html(entry.url)
    if rendered is not None:
        return extract_html(rendered, source)
    _media, charset = parse_content_type(entry.content_type)
    return extract_html(raw.decode(charset or "utf-8", errors="replace"), source)


def _quotable_blocks(doc: Document) -> list[Block]:
    """Return content blocks substantial enough to host a specific-detail quote.

    Headings and tiny blocks are skipped (they rarely carry a verifiable detail);
    paragraphs, tables, code and lists within a sane length window are kept, in
    document order, so block selection is deterministic.
    """
    out: list[Block] = []
    for block in doc.blocks:
        if block.type == "heading":
            continue
        length = len(block.text)
        if _MIN_BLOCK_CHARS <= length <= _MAX_BLOCK_CHARS:
            out.append(block)
    return out


def _draft_prompt(entry: ManifestEntry, block: Block, *, unanswerable: bool) -> str:
    """Build the fixed drafting prompt for one block (answerable or not)."""
    heading = " > ".join(block.heading_path) if block.heading_path else "(top level)"
    if unanswerable:
        instruction = (
            "Write ONE question about this topic whose answer is NOT present in "
            "the excerpt below (so a reader with only this text must answer "
            "INSUFFICIENT). Return JSON "
            '{"question": "...", "answer": "INSUFFICIENT", "quote": ""}.'
        )
    else:
        instruction = (
            "Write ONE question answerable ONLY from the excerpt, targeting a "
            "SPECIFIC detail (a parameter, default value, limit, header, status "
            "code, flag, version or exact name) — not a summary. Give the short "
            "exact answer, and a QUOTE copied VERBATIM from the excerpt that "
            'contains the answer. Return JSON {"question": "...", "answer": '
            '"...", "quote": "<verbatim substring of the excerpt>"}.'
        )
    return (
        f"You are drafting a benchmark question about {entry.site} "
        f"documentation (section: {heading}).\n"
        f"{instruction}\n"
        "The quote must be an exact substring of the excerpt. "
        "Return ONLY the JSON object.\n\n"
        f"Excerpt:\n{block.text}"
    )


def _parse_draft_json(text: str) -> dict[str, str] | None:
    """Parse the model's JSON object, tolerating code fences; ``None`` on failure."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        cleaned = cleaned[cleaned.find("{") : cleaned.rfind("}") + 1]
    try:
        obj = json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(obj, dict):
        return None
    return {str(k): str(v) for k, v in obj.items()}


def _quote_in_block(quote: str, block: Block) -> bool:
    """True if ``quote`` occurs in ``block.text`` modulo whitespace runs."""
    if not quote:
        return False
    norm_quote = _WS_RE.sub(" ", quote).strip()
    norm_block = _WS_RE.sub(" ", block.text).strip()
    return len(norm_quote) >= 12 and norm_quote in norm_block


def draft(
    store: CorpusStore,
    sites: object = None,
    *,
    provider: LLMProvider,
) -> list[Task]:
    """Draft specific-detail tasks from the clean IR of each ``ok`` corpus entry.

    ``sites`` is accepted for signature parity with the human workflow; the
    canonical category comes from IR signals and each entry's recorded
    ``rich_categories``. ``provider`` is the answerer LLM that phrases the
    question. Up to :data:`_TASKS_PER_ENTRY` drafts are produced per entry, each
    from a distinct quotable block. Answerable drafts whose returned quote cannot
    be located verbatim in the block are DISCARDED, so every stored quote is
    verifiable (SPEC §10.4). All drafts are ``author="ai_draft"``,
    ``validated=False``.
    """
    tasks: list[Task] = []
    draft_index = 0

    for entry in store.entries():
        if entry.status != "ok":
            continue

        doc = _document_for(store, entry)
        blocks = _quotable_blocks(doc)
        if not blocks:
            continue

        # Spread the picked blocks across the document deterministically.
        step = max(1, len(blocks) // _TASKS_PER_ENTRY)
        picked = blocks[::step][:_TASKS_PER_ENTRY]

        for block in picked:
            unanswerable = draft_index % _INSUFFICIENT_EVERY == _INSUFFICIENT_EVERY - 1
            prompt = _draft_prompt(entry, block, unanswerable=unanswerable)
            completion = provider.complete(prompt, response_format="json")
            parsed = _parse_draft_json(completion.text)
            if parsed is None or not parsed.get("question"):
                continue

            category = _category_for_entry(entry, doc, block)

            if unanswerable:
                task = Task(
                    id=f"draft-{draft_index:04d}",
                    category=category,
                    split="dev",
                    urls=[entry.url],
                    question=parsed["question"].strip(),
                    answer_type="judge",
                    expected=["INSUFFICIENT"],
                    expected_evidence=[],
                    author="ai_draft",
                    validated=False,
                )
                tasks.append(task)
                draft_index += 1
                continue

            quote = parsed.get("quote", "")
            if not _quote_in_block(quote, block):
                # Unverifiable quote → discard rather than store page chrome.
                continue
            answer = parsed.get("answer", "").strip()
            if not answer:
                continue

            task = Task(
                id=f"draft-{draft_index:04d}",
                category=category,
                split="dev",
                urls=[entry.url],
                question=parsed["question"].strip(),
                answer_type="judge",
                expected=[answer],
                expected_evidence=[Evidence(url=entry.url, quote=quote.strip())],
                author="ai_draft",
                validated=False,
            )
            tasks.append(task)
            draft_index += 1

    return tasks


def _parse_dt(value: str):  # type: ignore[no-untyped-def]
    """Parse an ISO timestamp from the manifest into a datetime."""
    from datetime import datetime

    return datetime.fromisoformat(value)


def main() -> None:  # pragma: no cover - human entry point, uses the real provider.
    """Human entry point: draft tasks with the real answerer provider.

    Spends API tokens; NOT run by the test suite. Wiring (store path, output
    path, provider construction) lives in ``bench/run_draft.py``.
    """
    raise SystemExit(
        "bench.draft_tasks.main is a human-run entry point; run "
        "`python -m bench.run_draft` which wires the real CorpusStore and "
        "OpenAIProvider."
    )


if __name__ == "__main__":
    main()
