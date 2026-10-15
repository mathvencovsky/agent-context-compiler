"""Offline tests for the M10 external task builder (bench/external_tasks.py)."""

from __future__ import annotations

from pathlib import Path

import httpx

from app.fetch.client import FetchResult
from app.fetch.render import RenderResult
from bench.corpus_store import CorpusStore
from bench.external_tasks import (
    build_tasks,
    cited_by_site,
    gold_evidence,
    in_dev_slice,
    pick_urls,
    stratified_order,
)
from bench.snapshot import SiteConfig, snapshot
from bench.tasks_schema import (
    Evidence,
    ExternalTask,
    Source,
    dump_external_tasks,
    load_external_tasks,
)


def _candidate(qid: int, site: str, cited: list[str], **extra: object) -> dict:
    return {
        "question_id": qid,
        "answer_id": qid + 1,
        "site": site,
        "cited_urls": cited,
        "task_type": "fact",
        "question_title": f"title {qid}",
        "question_text": f"question {qid}",
        "answer_text": f"answer {qid}",
        "answer_blockquotes": [],
        "question_owner_id": 7,
        "question_owner_name": "Ada",
        "answer_owner_id": None,
        "answer_owner_name": "Gone",
        "question_url": f"https://stackoverflow.com/q/{qid}",
        "answer_url": f"https://stackoverflow.com/a/{qid + 1}",
        "question_license": "CC BY-SA 4.0",
        "answer_license": "CC BY-SA 4.0",
        "question_created": "2023-05-02T10:00:00.000",
    } | extra


def test_stratified_order_interleaves_sites_and_is_deterministic() -> None:
    cands = [_candidate(q, "a", []) for q in (1, 2, 3)]
    cands += [_candidate(4, "b", [])]
    cands += [_candidate(q, "c", []) for q in (5, 6)]
    order = [c["question_id"] for c in stratified_order(cands)]

    # Any prefix keeps equal quotas: one per site per round, b runs out first.
    first_round = {
        next(c["site"] for c in cands if c["question_id"] == q) for q in order[:3]
    }
    assert first_round == {"a", "b", "c"}
    assert sorted(order) == [1, 2, 3, 4, 5, 6]
    assert order == [c["question_id"] for c in stratified_order(cands[::-1])]


def test_pick_urls_keeps_cited_pages_and_fills_from_pool_then_fallback() -> None:
    cited = ["https://d/1", "https://d/2"]
    pool = {"https://d/1", "https://d/3"}  # d/1 is the task's own page
    fallback = ["https://d/4", "https://d/5", "https://d/2"]
    urls = pick_urls("so-1", cited, pool, fallback)
    assert sorted(urls) == [
        "https://d/1",
        "https://d/2",
        "https://d/3",
        "https://d/4",
        "https://d/5",
    ]
    assert urls == pick_urls("so-1", cited, pool, fallback)
    assert len(pick_urls("so-2", ["https://d/1"], set(), [])) == 1  # nothing to add


def test_gold_evidence_matches_quotes_against_cited_pages() -> None:
    pages = {"https://d/1": "Intro. The END parameter defaults to a   newline. More."}
    evidence = gold_evidence(
        ["end parameter defaults to a newline", "not there"], pages
    )
    assert evidence == [
        Evidence(url="https://d/1", quote="end parameter defaults to a newline")
    ]


def test_build_tasks_drops_unsnapshotted_and_uses_final_urls(tmp_path: Path) -> None:
    def fake_fetch(url: str) -> FetchResult:
        if url.endswith("/gone"):
            from app.fetch.errors import RobotsDisallowed

            raise RobotsDisallowed("blocked", details={"url": url})
        final = url.replace("old.test", "new.test")
        return FetchResult(
            final_url=final,
            status=200,
            headers=httpx.Headers({"content-type": "text/html"}),
            body=b"<p>The end parameter defaults to a newline.</p>",
            content_type="text/html",
            redirects=int(final != url),
            elapsed_ms=1,
        )

    def fake_render(url: str) -> RenderResult:
        return RenderResult(final_url=url, html="", status=200)

    site = SiteConfig(
        site="Docs",
        key="docs",
        rich_categories=["guide"],
        render="never",
        urls=["https://old.test/p", "https://new.test/q", "https://old.test/gone"],
    )
    snapshot([site], tmp_path, fetch_fn=fake_fetch, render_fn=fake_render)
    store = CorpusStore(tmp_path)
    ordered = [
        _candidate(
            10,
            "docs",
            ["https://old.test/p"],
            answer_blockquotes=["the end parameter defaults to a newline"],
        ),
        _candidate(20, "docs", ["https://old.test/gone"]),  # rule 5
        _candidate(30, "docs", ["https://new.test/q"]),
    ]
    tasks, removed = build_tasks(ordered, store, {"docs": []})

    assert removed == 1
    assert [t.id for t in tasks] == ["so-10", "so-30"]
    first = tasks[0]
    assert first.split == "pilot"
    assert first.cited_urls == ["https://new.test/p"]  # after redirect
    assert sorted(first.urls) == ["https://new.test/p", "https://new.test/q"]
    assert first.expected_evidence[0].url == "https://new.test/p"
    assert first.source.question_author == "Ada (https://stackoverflow.com/users/7)"
    assert first.source.answer_author == "Gone"
    assert first.source.created == "2023-05-02"
    assert store.content_type("https://new.test/p") == "text/html"


def test_external_tasks_yaml_round_trip(tmp_path: Path) -> None:
    task = ExternalTask(
        id="so-1",
        split="holdout",
        site="python",
        task_type="code",
        urls=["https://docs.python.org/3/"],
        cited_urls=["https://docs.python.org/3/"],
        question_title="t",
        question="line one\nline two",
        reference_answer="use `x`",
        expected_evidence=[],
        source=Source(
            question_url="https://stackoverflow.com/q/1",
            answer_url="https://stackoverflow.com/a/2",
            question_author="Ada",
            answer_author="Linus",
            license="CC BY-SA 4.0",
            created="2023-01-02",
        ),
    )
    path = tmp_path / "external.yaml"
    dump_external_tasks([task], path)
    assert path.read_text(encoding="utf-8").startswith("# M10 external tasks")
    assert load_external_tasks(path) == [task]


def test_cited_by_site_groups_distinct_urls_and_skips_excluded() -> None:
    a, old = "https://docs.python.org/3/a.html", "https://docs.python.org/3.8/b.html"
    mdn = "https://developer.mozilla.org/en-US/docs/X"
    ordered = [_candidate(1, "python", [a, mdn]), _candidate(2, "python", [old, a])]
    assert cited_by_site(ordered, {old}) == {"python": [a], "mdn": [mdn]}


def test_dev_slice_is_seeded_and_about_a_quarter() -> None:
    ids = [f"so-{n}" for n in range(4000)]
    picks = [in_dev_slice(i) for i in ids]
    assert 0.22 < sum(picks) / len(picks) < 0.28
    assert picks == [in_dev_slice(i) for i in ids]
    assert picks != [in_dev_slice(i, seed=1) for i in ids]
