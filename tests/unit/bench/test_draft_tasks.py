"""Draft with the FAKE provider over the fixture corpus: provenance + literal quotes."""

from __future__ import annotations

import pytest

from app.providers.llm.fake import FakeLLMProvider
from bench.corpus_store import CorpusStore
from bench.draft_tasks import draft, rich_label_to_category
from bench.tasks_schema import Category


@pytest.mark.parametrize(
    ("labels", "expected"),
    [
        (["api_reference", "guide"], "api_reference"),
        (["http_reference"], "api_reference"),
        (["guide", "tutorial"], "guide"),
        (["spa_js", "guide"], "guide"),
        (["configuration_reference"], "params_table"),
        (["build_reference"], "params_table"),
        (["changelog"], "changelog"),
        (["release_notes"], "changelog"),
        (["pdf", "standards"], "pdf"),
        (["protocol_specification"], "pdf"),
        (["unknown_label"], "guide"),  # total fallback
    ],
)
def test_rich_label_mapping(labels: list[str], expected: Category) -> None:
    assert rich_label_to_category(labels) == expected


def test_mapping_covers_all_five_canonical_categories() -> None:
    label_sets = [
        ["api_reference"],
        ["guide"],
        ["configuration_reference"],
        ["changelog"],
        ["pdf"],
    ]
    produced = {rich_label_to_category(labels) for labels in label_sets}
    assert produced == {"api_reference", "guide", "params_table", "changelog", "pdf"}


def test_draft_produces_ai_draft_unvalidated_with_literal_quotes(
    store: CorpusStore,
) -> None:
    provider = FakeLLMProvider()
    tasks = draft(store, provider=provider)

    assert tasks, "expected at least one drafted task from the fixture corpus"
    for task in tasks:
        assert task.author == "ai_draft"
        assert task.validated is False
        assert task.question  # the fake phrased a question
        # Every evidence quote must exist LITERALLY in its source document text.
        for evidence in task.expected_evidence:
            raw = store.raw_bytes(evidence.url).decode("utf-8")
            # The quote is derived from tag-stripped text, so compare against a
            # whitespace-collapsed, tag-free view of the source.
            import re

            flat = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw)).strip()
            assert evidence.quote in flat


def test_draft_is_deterministic(store: CorpusStore) -> None:
    provider = FakeLLMProvider()
    first = draft(store, provider=provider)
    second = draft(store, provider=provider)
    assert [t.model_dump() for t in first] == [t.model_dump() for t in second]
