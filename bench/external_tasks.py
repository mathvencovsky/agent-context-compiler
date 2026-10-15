"""Build the M10 external task set from mined candidates (PREREG §2.1, SPEC §10.6).

Input: ``bench/results/m10/candidates.jsonl`` from :mod:`bench.mine_dump`.
Every step is deterministic:

1. Put candidates in one random order (seed 20261015), stratified by site: each
   site's candidates are shuffled and the sites interleaved round-robin, so any
   prefix holds equal quotas and a site that runs out leaves its share to the
   others. ``--limit`` keeps only the first N of that order.
2. Freeze every cited page of those candidates into ``bench/corpus/`` in append
   mode, so the pages under the existing tasks never change. Rule 5: a
   candidate whose cited pages all fail to snapshot is dropped and counted.
3. The first 20 survivors are ``pilot``, the rest ``holdout``.
4. Each task gets its cited pages (after redirect, at most 4) plus same-site
   distractors up to 5 URLs, shuffled with the task id as seed, and gold
   evidence: the accepted answer's blockquotes that match a cited page at
   ratio >= 0.9 (the SPEC §10.4 matcher).

Output: ``bench/tasks/external.yaml`` (CC BY-SA, with attribution) and
``bench/results/m10/build_stats.json``. Reaches the public web (the snapshot);
NOT run by the test suite.

Usage:
    uv run python -m bench.external_tasks --limit 1200
"""

from __future__ import annotations

import argparse
import json
import random
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from bench.corpus_store import CorpusStore
from bench.grade import _CITATION_RATIO, _best_ratio, _flatten_html
from bench.mine_dump import site_for_url
from bench.snapshot import DEFAULT_CORPUS_DIR, SiteConfig, load_sites, snapshot
from bench.tasks_schema import Evidence, ExternalTask, Source, dump_external_tasks

#: PREREG §2.1 sampling seed.
SEED = 20261015
#: PREREG §2.1: the first tasks in the random order form the pilot split.
PILOT_SIZE = 20
#: PREREG §2.1: URLs per task, of which at most ``MAX_CITED`` are cited pages.
URLS_PER_TASK = 5
MAX_CITED = 4

_TASKS_PATH = Path(__file__).resolve().parent / "tasks" / "external.yaml"


def stratified_order(
    candidates: Sequence[Mapping[str, Any]], seed: int = SEED
) -> list[Mapping[str, Any]]:
    """One random order, stratified by site with equal quotas (PREREG §2.1)."""
    rng = random.Random(seed)
    by_site: dict[str, list[Mapping[str, Any]]] = {}
    for c in sorted(candidates, key=lambda c: c["question_id"]):
        by_site.setdefault(c["site"], []).append(c)
    sites = sorted(by_site)
    for site in sites:
        rng.shuffle(by_site[site])
    rng.shuffle(sites)
    longest = max((len(v) for v in by_site.values()), default=0)
    return [
        by_site[site][i]
        for i in range(longest)
        for site in sites
        if i < len(by_site[site])
    ]


def pick_urls(
    task_id: str, cited: Sequence[str], pool: set[str], fallback: Sequence[str]
) -> list[str]:
    """Cited pages plus same-site distractors, shuffled (PREREG §2.1).

    Distractors come from ``pool`` (pages other tasks of the site cite), then
    from ``fallback`` (the site's ``sites.yaml`` pages) if the pool runs short.
    """
    rng = random.Random(task_id)
    own = list(cited[:MAX_CITED])
    need = URLS_PER_TASK - len(own)
    first = sorted(pool - set(own))
    picks = rng.sample(first, min(need, len(first)))
    rest = sorted(set(fallback) - set(own) - set(picks))
    picks += rng.sample(rest, min(need - len(picks), len(rest)))
    urls = own + picks
    rng.shuffle(urls)
    return urls


def gold_evidence(quotes: Sequence[str], pages: Mapping[str, str]) -> list[Evidence]:
    """Blockquotes found in a cited page's text, as evidence for that page."""
    evidence: list[Evidence] = []
    for quote in quotes:
        for url, text in pages.items():
            if _best_ratio(quote, text) >= _CITATION_RATIO:
                evidence.append(Evidence(url=url, quote=quote))
                break
    return evidence


def _author(name: str | None, user_id: int | None) -> str:
    """Display name with a profile link, as the dump's license.txt asks."""
    if user_id is None:
        return name or "unknown"
    profile = f"https://stackoverflow.com/users/{user_id}"
    return f"{name or f'user {user_id}'} ({profile})"


def _page_text(store: CorpusStore, url: str) -> str:
    rendered = store.rendered_html(url)
    if rendered is not None:
        return _flatten_html(rendered.encode("utf-8"), "text/html; charset=utf-8")
    return _flatten_html(store.raw_bytes(url), store.content_type(url))


def build_tasks(
    ordered: Sequence[Mapping[str, Any]],
    store: CorpusStore,
    fallback: Mapping[str, Sequence[str]],
) -> tuple[list[ExternalTask], int]:
    """Turn ordered candidates into tasks; also return how many rule 5 dropped."""
    final_url = {e.url: e.final_url for e in store.entries() if e.status == "ok"}
    kept: list[tuple[Mapping[str, Any], list[str]]] = []
    for c in ordered:
        cited = list(
            dict.fromkeys(final_url[u] for u in c["cited_urls"] if u in final_url)
        )
        if cited:
            kept.append((c, cited))
    pools: dict[str, set[str]] = {}
    for c, cited in kept:
        pools.setdefault(c["site"], set()).update(cited)

    tasks = []
    for i, (c, cited) in enumerate(kept):
        task_id = f"so-{c['question_id']}"
        pages = {url: _page_text(store, url) for url in cited[:MAX_CITED]}
        license_ = c["question_license"]
        if c["answer_license"] != license_:
            license_ = f"question {license_}; answer {c['answer_license']}"
        tasks.append(
            ExternalTask(
                id=task_id,
                split="pilot" if i < PILOT_SIZE else "holdout",
                site=c["site"],
                task_type=c["task_type"],
                urls=pick_urls(task_id, cited, pools[c["site"]], fallback[c["site"]]),
                cited_urls=cited,
                question_title=c["question_title"],
                question=c["question_text"],
                reference_answer=c["answer_text"],
                expected_evidence=gold_evidence(c["answer_blockquotes"], pages),
                source=Source(
                    question_url=c["question_url"],
                    answer_url=c["answer_url"],
                    question_author=_author(
                        c["question_owner_name"], c["question_owner_id"]
                    ),
                    answer_author=_author(c["answer_owner_name"], c["answer_owner_id"]),
                    license=license_,
                    created=c["question_created"][:10],
                ),
            )
        )
    return tasks, len(ordered) - len(kept)


def main(argv: list[str] | None = None) -> None:  # pragma: no cover - network
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--candidates", default="bench/results/m10/candidates.jsonl")
    parser.add_argument("--corpus", default=str(DEFAULT_CORPUS_DIR))
    parser.add_argument("--out", default=str(_TASKS_PATH))
    parser.add_argument("--limit", type=int, help="keep the first N of the order")
    args = parser.parse_args(argv)

    lines = Path(args.candidates).read_text(encoding="utf-8").splitlines()
    ordered = stratified_order([json.loads(line) for line in lines])[: args.limit]

    sites = {s.key: s for s in load_sites()}
    cited_by_site: dict[str, list[str]] = {}
    for c in ordered:
        for url in c["cited_urls"]:
            cited_by_site.setdefault(site_for_url(url) or c["site"], []).append(url)
    snapshot(
        [
            SiteConfig(
                site=sites[key].site,
                key=key,
                rich_categories=sites[key].rich_categories,
                render=sites[key].render,
                urls=list(dict.fromkeys(urls)),
            )
            for key, urls in cited_by_site.items()
        ],
        args.corpus,
        append=True,
    )

    store = CorpusStore(args.corpus)
    ok = {e.url: e.final_url for e in store.entries() if e.status == "ok"}
    fallback = {k: [ok[u] for u in s.urls if u in ok] for k, s in sites.items()}
    tasks, rule5_removed = build_tasks(ordered, store, fallback)
    dump_external_tasks(tasks, args.out)

    stats = {
        "considered": len(ordered),
        "removed_rule5_no_snapshot": rule5_removed,
        "tasks": len(tasks),
        "by_split": Counter(t.split for t in tasks),
        "by_site": Counter(t.site for t in tasks),
        "by_task_type": Counter(t.task_type for t in tasks),
        "with_gold_evidence": sum(1 for t in tasks if t.expected_evidence),
    }
    stats_path = Path(args.candidates).with_name("build_stats.json")
    stats_path.write_text(json.dumps(stats, indent=2), encoding="utf-8")
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
