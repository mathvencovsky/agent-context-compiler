"""Draft benchmark tasks with the answerer LLM (SPEC §10.2).

``draft_tasks`` seeds ``tasks.yaml`` with AI-authored *drafts* a human later
reviews (``bench/review_tasks.py``). Each draft:

* is tied to one corpus URL and a canonical task category (the five of SPEC
  §10.2), chosen by mapping the site's rich labels through
  :func:`rich_label_to_category`;
* has its question written by the injected answerer :class:`LLMProvider` (the
  deterministic fake in tests, the real OpenAI model in the human run);
* carries an :class:`~bench.tasks_schema.Evidence` whose ``quote`` is copied
  LITERALLY from the snapshot document — a draft whose quote cannot be located
  in the source text is DISCARDED, so every quote is verifiable (SPEC §10.4);
* is marked ``author="ai_draft"``, ``validated=False``;
* is unanswerable (``expected=["INSUFFICIENT"]``) for roughly 10% of drafts, so
  the reviewed set exercises the INSUFFICIENT path.

Nothing here touches the network: the provider is injected and the text comes
from the frozen :class:`~bench.corpus_store.CorpusStore`. The real run uses the
real answerer; the tests use the fake.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

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
    "guide": "guide",
    "tutorial": "guide",
    "troubleshooting": "guide",
    "storage": "guide",
    "spa_js": "guide",
    "configuration_reference": "params_table",
    "build_reference": "params_table",
    "changelog": "changelog",
    "release_notes": "changelog",
    "pdf": "pdf",
    "standards": "pdf",
    "protocol_specification": "pdf",
}

#: Fraction of drafts made unanswerable (``expected=["INSUFFICIENT"]``).
_INSUFFICIENT_EVERY = 10

#: A quote is this many characters of literal source text.
_QUOTE_LEN = 80

#: Collapse runs of whitespace when deriving readable text from HTML.
_WS_RE = re.compile(r"\s+")
_TAG_RE = re.compile(r"<[^>]+>")


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


def _entry_text(store: CorpusStore, entry: ManifestEntry) -> str:
    """Return readable text for ``entry`` from the frozen corpus.

    PDFs are left as-is (their bytes are not HTML); for HTML the rendered copy is
    preferred when present, else the raw body is decoded and stripped of tags.
    Whitespace is collapsed so a derived quote is a clean literal substring.
    """
    media = entry.content_type
    if media not in ("text/html", "application/xhtml+xml"):
        raw = store.raw_bytes(entry.url)
        return _WS_RE.sub(" ", raw.decode("utf-8", errors="replace")).strip()

    rendered = store.rendered_html(entry.url)
    if rendered is not None:
        html = rendered
    else:
        raw = store.raw_bytes(entry.url)
        _m, charset = parse_content_type(media)
        html = raw.decode(charset or "utf-8", errors="replace")
    stripped = _TAG_RE.sub(" ", html)
    return _WS_RE.sub(" ", stripped).strip()


def _derive_quote(text: str) -> str | None:
    """Return a literal substring of ``text`` to use as evidence, or ``None``.

    The quote is a deterministic window of :data:`_QUOTE_LEN` characters taken
    from the first non-trivial content. ``None`` when the source has too little
    text to quote (that draft is discarded).
    """
    if len(text) < _QUOTE_LEN:
        return None
    return text[:_QUOTE_LEN]


def draft(
    store: CorpusStore,
    sites: object = None,
    *,
    provider: LLMProvider,
) -> list[Task]:
    """Draft one AI task per ``ok`` corpus entry with a verifiable quote.

    ``sites`` is accepted for signature parity with the human workflow (the
    canonical category is derived from each entry's ``rich_categories`` recorded
    in the manifest); it is unused here. ``provider`` is the answerer LLM used to
    phrase the question. Every returned task is ``author="ai_draft"``,
    ``validated=False`` and carries a quote that exists literally in its source
    document; drafts whose quote cannot be derived are skipped.
    """
    tasks: list[Task] = []
    draft_index = 0
    for entry in store.entries():
        if entry.status != "ok":
            continue

        text = _entry_text(store, entry)
        quote = _derive_quote(text)
        if quote is None:
            continue

        category = rich_label_to_category(entry.rich_categories)
        is_insufficient = draft_index % _INSUFFICIENT_EVERY == _INSUFFICIENT_EVERY - 1

        prompt = (
            "Write one specific-detail question answerable only from this "
            f"documentation excerpt about {entry.site}:\n\n{quote}"
        )
        completion = provider.complete(prompt, temperature=0.0, response_format="text")
        question = completion.text.strip() or f"What does {entry.site} document here?"

        task = Task(
            id=f"draft-{draft_index:04d}",
            category=category,
            split="dev",
            urls=[entry.url],
            question=question,
            answer_type="judge",
            expected=["INSUFFICIENT"] if is_insufficient else [quote],
            expected_evidence=(
                [] if is_insufficient else [Evidence(url=entry.url, quote=quote)]
            ),
            author="ai_draft",
            validated=False,
        )
        tasks.append(task)
        draft_index += 1

    return tasks


def main() -> None:  # pragma: no cover - human entry point, uses the real provider.
    """Human entry point: draft tasks with the real answerer provider.

    Spends API tokens; NOT run by the test suite. Wiring (store path, output
    path, provider construction) is intentionally left to the human operator who
    runs the snapshot first.
    """
    raise SystemExit(
        "bench.draft_tasks.main is a human-run entry point; wire the real "
        "CorpusStore and OpenAIProvider before calling it."
    )


if __name__ == "__main__":
    main()
