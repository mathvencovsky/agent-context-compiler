"""Offline tests for the M10 judge (bench/judge_external.py)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import httpx

from app.fetch.client import FetchResult
from app.fetch.render import RenderResult
from app.providers.llm.base import LLMResult
from bench.answer import Citation
from bench.corpus_store import CorpusStore
from bench.grade import _grade_citations
from bench.judge_external import (
    RUBRIC,
    blind,
    final_answer,
    flip_rate,
    in_recheck_sample,
    judge,
    judge_prompt,
)
from bench.snapshot import SiteConfig, snapshot
from bench.tasks_schema import Evidence, ExternalTask, Source

_PREREG = Path(__file__).resolve().parents[3] / "bench" / "PREREG.md"


class ScriptedJudge:
    """Returns the scripted replies in order and records each seed it got."""

    model = "scripted-judge"

    def __init__(self, *replies: str) -> None:
        self.replies = list(replies)
        self.seeds: list[int] = []

    def complete(self, prompt: str, *, seed: int = 7, response_format: str = "text"):
        self.seeds.append(seed)
        return LLMResult(
            text=self.replies.pop(0), input_tokens=1, output_tokens=1, model=self.model
        )


def _task(**extra: object) -> ExternalTask:
    fields = {
        "id": "so-1",
        "split": "holdout",
        "site": "python",
        "task_type": "fact",
        "urls": ["https://d.test/p"],
        "cited_urls": ["https://d.test/p"],
        "question_title": "Why a newline?",
        "question": "print adds a newline",
        "reference_answer": "Pass end='' to print.",
        "expected_evidence": [],
        "source": Source(
            question_url="https://stackoverflow.com/q/1",
            answer_url="https://stackoverflow.com/a/2",
            question_author="Ada",
            answer_author="Linus",
            license="CC BY-SA 4.0",
            created="2023-01-02",
        ),
    }
    return ExternalTask(**(fields | extra))


def test_rubric_is_the_prereg_appendix_b_text_verbatim() -> None:
    text = _PREREG.read_text(encoding="utf-8")
    block = re.search(
        r"Judge rubric, used by both judges and by the human graders:"
        r"\s*```\n(.*?)\n```",
        text,
        re.DOTALL,
    )
    assert block is not None
    assert block.group(1) == RUBRIC


def test_judge_prompt_fills_slots_once() -> None:
    task = _task(question="see <accepted answer> below")
    prompt = judge_prompt(task, "use end=''")
    assert "see <accepted answer> below" in prompt  # question text left alone
    assert "REFERENCE:\nPass end='' to print." in prompt
    assert prompt.endswith("CANDIDATE:\nuse end=''")


def test_final_answer_takes_the_last_answer_object_or_the_whole_message() -> None:
    message = (
        'Draft: {"answer": "old"}\nFinal:\n```json\n'
        '{"answer": "Pass end=\'\'", "citations": [{"url": "u", "quote": "q"}]}\n```'
    )
    result = final_answer(message)
    assert result.answer == "Pass end=''"
    assert result.citations == [Citation(url="u", quote="q")]
    assert (
        final_answer("  just prose, no JSON {broken  ").answer
        == "just prose, no JSON {broken"
    )


def test_judge_retries_unparseable_replies_with_new_seeds() -> None:
    provider = ScriptedJudge(
        "not json", '{"correct": "yes"}', '{"correct": true, "reason": "same fix"}'
    )
    verdict = judge(_task(), "use end=''", provider)
    assert (verdict.correct, verdict.reason, verdict.attempts) == (True, "same fix", 3)
    assert provider.seeds == [7, 8, 9]

    gives_up = judge(_task(), "x", ScriptedJudge("no", "no", "no"))
    assert (gives_up.correct, gives_up.attempts) == (None, 3)


def test_recheck_sample_and_flip_rate() -> None:
    picks = [in_recheck_sample(f"so-{n}", "nina") for n in range(3000)]
    assert 0.08 < sum(picks) / len(picks) < 0.12
    assert picks == [in_recheck_sample(f"so-{n}", "nina") for n in range(3000)]
    assert (
        flip_rate([(True, True), (True, False), (None, True), (False, False)]) == 1 / 3
    )


def test_spec_citation_check_works_for_external_tasks(tmp_path: Path) -> None:
    def fake_fetch(url: str) -> FetchResult:
        return FetchResult(
            final_url=url,
            status=200,
            headers=httpx.Headers({"content-type": "text/html"}),
            body=b"<p>print(*objects, sep=' ', end='\\n')</p>",
            content_type="text/html",
            redirects=0,
            elapsed_ms=1,
        )

    def fake_render(url: str) -> RenderResult:
        return RenderResult(final_url=url, html="", status=200)

    site = SiteConfig(
        site="D", key="d", rich_categories=[], render="never", urls=["https://d.test/p"]
    )
    snapshot([site], tmp_path, fetch_fn=fake_fetch, render_fn=fake_render)
    quote = "print(*objects, sep=' ', end='\\n')"
    task = _task(expected_evidence=[Evidence(url="https://d.test/p", quote=quote)])
    reply = {"answer": "a", "citations": [{"url": "https://d.test/p", "quote": quote}]}
    cited = final_answer(json.dumps(reply))
    assert _grade_citations(task, cited, store=CorpusStore(tmp_path))


def test_blind_masks_arm_revealing_names_in_the_candidate() -> None:
    text = "Per assemble_context and WebFetch (NINA, Context7, query-docs): use end=''"
    assert blind(text) == "Per [tool] and [tool] ([tool], [tool], [tool]): use end=''"
    assert judge_prompt(_task(), text).endswith(
        "CANDIDATE:\nPer [tool] and [tool] ([tool], [tool], [tool]): use end=''"
    )
    assert blind("via web_fetch") == "via [tool]"
    assert blind("the pagination API") == "the pagination API"  # whole words only
