"""Does NINA beat Context7 on the same documentation questions?

Half B of the keyed tasks (not used to tune NINA), the first :data:`SAMPLE` in a
fixed hash order. Both tools work live and are given the same short question (the
task's title). Three arms:

* ``nina``: the running local API, web scope settings (compact, 1000 tokens), on
  the page that holds the answer. Local models, no paid call.
* ``c7_fetch``: Context7 ``GET /api/v3/fetch`` on the same page: the same job.
* ``c7_docs``: Context7 ``GET /api/v2/context`` on the site's library
  (:data:`LIBRARIES`, the first result of its library search, fixed before
  measuring): what its agents usually call. It does not get the page.

No key and no account: Context7's free quota only (200 requests a month). Every
response is stored, so a rerun never spends the quota twice.

**Being handed the right page favors the page readers.** Real agents must find it.
All three arms are live, so a page may have changed since the keys were made.

**Answer hit.** ``answer_hit`` of :mod:`bench.compact_eval` needs a key sentence
literally, and Context7 reformats text, so it would be unfair. :func:`soft_hit`
counts a key sentence as present when at least :data:`SOFT_SHARE` of its words of
3+ letters are within :data:`WINDOW_LINES` consecutive non-empty lines of the reply.
Where the two arms of a comparison disagree, a blind reader decides from the
task's reference answer whether each reply answers it (:func:`blind_sheet`; labels
shuffled, so the reader does not know which tool is which), and that decision
replaces the soft hit of those tasks.

**Verdict (written before measuring, DECISIONS.md 2026-11-04),** for ``nina``
against each Context7 arm. ``d`` is the hit difference with its 95% paired bootstrap
CI; ``share`` is NINA's mean tokens over the other arm's mean tokens.

* ``NINA VENCE``: the CI's low end is above 0 and ``share`` <= 1.10; or ``d`` >= -0.03
  and ``share`` <= 0.70.
* ``NINA PERDE``: the CI's high end is below 0; or ``d`` <= 0.03 and ``share`` >= 1.20.
* ``SEM VANTAGEM``: anything else. With this sample, a gap under about 15 points
  can land here.

Only ``NINA VENCE`` against ``c7_fetch`` is evidence that NINA is worth choosing for
public documentation. Anything else means no shown reason to pick it over a free
product already on the market. This judges answer presence and tokens on public
documentation only, not private documents, provenance or a model's final answer.

**Token sweep (written before measuring, DECISIONS.md 2026-11-04).** The first
verdict was ``SEM VANTAGEM`` at 1.85x the tokens, and 35% of NINA's reply was tags
and notes. After the leaner compact form, :data:`BUDGETS` run on these same 80
tasks with Context7's stored replies (no Context7 call). A budget passes if NINA's
mean tokens are at most :data:`MAX_TOKENS_SEEN` (110% of ``c7_fetch``'s 509) and its
soft hit is not below ``c7_fetch``'s 0.450; the highest soft hit wins, then fewest
tokens. None passing kills this bet, with no retuning. These tasks were already
seen, so a pass only earns the blind review and a check on new tasks.

**Final check (written before measuring, DECISIONS.md 2026-11-04).** The sweep's
winner was budget 560 (soft hit 0.613, 535 tokens). Its disagreements with
Context7 are read blind together with :data:`NEW_TASKS` tasks never used here,
the next in the same hash order, against ``c7_fetch`` only (all of the free quota
left). The budget becomes the web scope's read if both hold:

1. on the 80 tasks, the verdict above against ``c7_fetch`` is ``NINA VENCE``;
2. on the new tasks, after blind reading, ``d`` >= 0 and NINA's mean tokens are at
   most 110% of ``c7_fetch``'s. So few tasks can refute this but not prove it.

    uv run python -m bench.context7_compare run
    uv run python -m bench.context7_compare new
    uv run python -m bench.context7_compare sheet2
    uv run python -m bench.context7_compare final
    uv run python -m bench.context7_compare sweep
    uv run python -m bench.context7_compare sheet
    uv run python -m bench.context7_compare verdict
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urldefrag

from bench.compact_eval import answer_units
from bench.m10a_diagnose import paired
from bench.section_recall import normalize, units

SAMPLE = 80
SOFT_SHARE = 0.8
WINDOW_LINES = 3
ARMS = ("nina", "c7_fetch", "c7_docs")
BUDGETS = (500, 560, 620, 700, 1000)
MAX_TOKENS_SEEN = 560
MIN_HIT_SEEN = 0.45
FINAL_BUDGET = 560
NEW_TASKS = 30
RIVALS = ("c7_fetch", "c7_docs")
#: The first result of Context7's library search per site, taken before measuring.
LIBRARIES = {
    "docs.python.org": "/python/cpython",
    "developer.mozilla.org": "/mdn/content",
    "www.postgresql.org": "/websites/postgresql",
    "docs.docker.com": "/docker/docs",
    "react.dev": "/reactjs/react.dev",
    "legacy.reactjs.org": "/reactjs/react.dev",
    "kubernetes.io": "/websites/kubernetes_io",
    "flask.palletsprojects.com": "/pallets/flask",
    "www.sqlite.org": "/websites/sqlite_docs",
}
_OUT = Path("bench/results/m10b/context7")
_C7 = "https://context7.com/api"
_WORD = re.compile(r"[^\W_]{3,}")


def sample(task_ids: Sequence[str], size: int = SAMPLE) -> list[str]:
    """The first ``size`` ids in a fixed order that favors no site or question."""
    return sorted(task_ids, key=lambda t: hashlib.sha256(t.encode()).hexdigest())[:size]


def _words(text: str) -> set[str]:
    return set(_WORD.findall(normalize(text)))


def soft_hit(section_units: Sequence[str], reply: str) -> bool:
    """A key sentence's words are in a few consecutive lines of ``reply``."""
    lines = [_words(line) for line in reply.splitlines() if line.strip()]
    windows = [set().union(*lines[i : i + WINDOW_LINES]) for i in range(len(lines))]
    for unit in answer_units(section_units):
        wanted = _words(unit)
        if wanted and any(
            len(wanted & window) >= SOFT_SHARE * len(wanted) for window in windows
        ):
            return True
    return False


def verdict(d: float, lo: float, hi: float, share: float) -> str:
    """The rule of the module docstring."""
    if (lo > 0 and share <= 1.10) or (d >= -0.03 and share <= 0.70):
        return "NINA VENCE"
    if hi < 0 or (d <= 0.03 and share >= 1.20):
        return "NINA PERDE"
    return "SEM VANTAGEM"


def scrub(text: str) -> str:
    """A reply without what names its tool; page text stays untouched.

    The notice goes first: it names the notes tag, and removing notes before it
    once erased whole replies.
    """
    text = re.sub(r"^\[untrusted_source\].*$", "", text, flags=re.M)
    text = re.sub(r"<nina_notes>.*?</nina_notes>", "", text, flags=re.S)
    text = re.sub(r"^coverage: .*$|^sources: \d+ ok, \d+ failed$", "", text, flags=re.M)
    text = re.sub(r"^Note: this page comes from the open web.*$", "", text, flags=re.M)
    text = re.sub(r"</?(document|fragment)\b[^>]*>", "", text)
    text = re.sub(r"\bNINA\b|Context7", "TOOL", text)
    return text.strip() or "(the tool returned nothing)"


def blind_sheet(
    rows: Sequence[Mapping[str, Any]], seed: int = 7
) -> tuple[list[dict[str, Any]], dict[str, dict[str, str]]]:
    """Each disagreement once per rival, labels shuffled: ``(sheet, secret key)``."""
    rng = random.Random(seed)
    sheet: list[dict[str, Any]] = []
    key: dict[str, dict[str, str]] = {}
    for row in rows:
        for rival in RIVALS:
            if rival not in row["hit"] or row["hit"]["nina"] == row["hit"][rival]:
                continue
            case = f"{row['task']}|{rival}"
            first, second = rng.sample(["nina", rival], 2)
            key[case] = {"A": first, "B": second}
            sheet.append(
                {
                    "case": hashlib.sha256(case.encode()).hexdigest()[:12],
                    "question": row["question"],
                    "reference_answer": row["reference"],
                    "A": row["reply"][first],
                    "B": row["reply"][second],
                }
            )
    return sheet, key


def adjusted(
    row: Mapping[str, Any],
    rival: str,
    decisions: Mapping[str, Mapping[str, bool]],
    key: Mapping[str, Mapping[str, str]],
) -> tuple[bool, bool]:
    """``(nina, rival)`` hits, the blind reader deciding a disagreement."""
    nina, other = row["hit"]["nina"], row["hit"][rival]
    case = f"{row['task']}|{rival}"
    code = hashlib.sha256(case.encode()).hexdigest()[:12]
    if nina == other or code not in decisions:
        return nina, other
    by_arm = {key[case][label]: decisions[code][label] for label in ("A", "B")}
    return by_arm["nina"], by_arm[rival]


def _context7(path: str, **params: str) -> dict[str, Any]:  # pragma: no cover - network
    url = f"{_C7}{path}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": "nina-bench/0.1"})
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            status, body = response.status, response.read().decode("utf-8", "replace")
            left = response.headers.get("RateLimit-Remaining")
    except urllib.error.HTTPError as exc:
        status, body = exc.code, exc.read().decode("utf-8", "replace")
        left = exc.headers.get("RateLimit-Remaining")
    except (urllib.error.URLError, TimeoutError) as exc:
        status, body, left = 0, type(exc).__name__, None
    seconds = time.perf_counter() - started
    if status == 429:
        raise SystemExit(f"Context7 quota reached (left: {left}); stored results stay")
    return {
        "status": status,
        "text": body if status == 200 else "",
        "error": "" if status == 200 else body[:200],
        "seconds": seconds,
        "left": left,
    }


def _nina(
    api: Any, question: str, url: str, max_tokens: int = 1000
) -> dict[str, Any]:  # pragma: no cover
    started = time.perf_counter()
    try:
        body = api.assemble(
            query=question,
            urls=[url],
            max_tokens=max_tokens,
            response_format="concise",
            compact=True,
        )
        status, text, error = 200, str(body["context"]), ""
    except Exception as exc:  # a refusal or timeout is a measured miss
        status, text, error = 0, "", f"{type(exc).__name__}: {exc}"[:200]
    seconds = time.perf_counter() - started
    return {"status": status, "text": text, "error": error, "seconds": seconds}


def _keys() -> list[dict[str, Any]]:  # pragma: no cover - local data
    from bench.anchor_qrels import in_tune_half

    lines = Path("bench/results/m10a/qrels.jsonl").read_text("utf-8").splitlines()
    half_b = {
        k["task_id"]: k
        for k in map(json.loads, lines)
        if not in_tune_half(k["task_id"])
    }
    return [half_b[t] for t in sample(list(half_b))]


def run() -> None:  # pragma: no cover - network and local API
    from app.mcp_server.client import NinaApiClient
    from bench.tasks_schema import load_external_tasks

    tasks = {t.id: t for t in load_external_tasks(Path("bench/tasks/external.yaml"))}
    api = NinaApiClient(base_url="http://127.0.0.1:8000", api_key="nina_local_dev")
    api.set_caller("teste-context7")
    _OUT.mkdir(parents=True, exist_ok=True)
    keys = _keys()
    for done, key in enumerate(keys):
        path = _OUT / f"{key['task_id']}.json"
        task = tasks[key["task_id"]]
        question = task.question_title
        url = urldefrag(key["url"])[0]
        host = urllib.parse.urlsplit(url).netloc
        replies = json.loads(path.read_text("utf-8")) if path.exists() else {}
        if "nina" not in replies:
            replies["nina"] = _nina(api, question, url)
        if "c7_fetch" not in replies:
            replies["c7_fetch"] = _context7("/v3/fetch", url=url, query=question)
        if "c7_docs" not in replies and host in LIBRARIES:
            replies["c7_docs"] = _context7(
                "/v2/context", libraryId=LIBRARIES[host], query=question, type="txt"
            )
        path.write_text(json.dumps(replies, ensure_ascii=False), encoding="utf-8")
        left = replies["c7_fetch"].get("left")
        print(f"{done + 1}/{len(keys)} {host} quota left {left}", flush=True)


def sweep_passes(tokens: float, hit: float) -> bool:
    """The sweep rule of the module docstring."""
    return tokens <= MAX_TOKENS_SEEN and hit >= MIN_HIT_SEEN


def sweep() -> None:  # pragma: no cover - local API and data
    from app.index.tokens import count_tokens
    from app.mcp_server.client import NinaApiClient

    api = NinaApiClient(base_url="http://127.0.0.1:8000", api_key="nina_local_dev")
    api.set_caller("teste-context7")
    rows = _rows()
    keys = {k["task_id"]: k for k in _keys()}
    lines = ["| budget | soft hit | mean tokens | median s | failed | passes |"]
    lines.append("|---|---|---|---|---|---|")
    for budget in BUDGETS:
        folder = _OUT / f"budget_{budget}"
        folder.mkdir(exist_ok=True)
        hits, tokens, seconds, failed = [], [], [], 0
        for row in rows:
            path = folder / f"{row['task']}.json"
            key = keys[row["task"]]
            if path.exists():
                reply = json.loads(path.read_text("utf-8"))
            else:
                url = urldefrag(key["url"])[0]
                reply = _nina(api, row["question"], url, budget)
                path.write_text(json.dumps(reply, ensure_ascii=False), "utf-8")
            hits.append(soft_hit(units(key["section"]), reply["text"]))
            tokens.append(count_tokens(reply["text"]))
            seconds.append(reply["seconds"])
            failed += reply["status"] != 200
        hit, mean = statistics.mean(hits), statistics.mean(tokens)
        lines.append(
            f"| {budget} | {hit:.3f} | {mean:,.0f} | "
            f"{statistics.median(seconds):.1f} | {failed} | "
            f"{'yes' if sweep_passes(mean, hit) else ''} |"
        )
        print(lines[-1], flush=True)
    text = "\n".join(lines) + "\n"
    (_OUT.parent / "context7_sweep.md").write_text(text, encoding="utf-8")


def _new_keys() -> list[dict[str, Any]]:  # pragma: no cover - local data
    from bench.anchor_qrels import in_tune_half

    lines = Path("bench/results/m10a/qrels.jsonl").read_text("utf-8").splitlines()
    half_b = {
        k["task_id"]: k
        for k in map(json.loads, lines)
        if not in_tune_half(k["task_id"])
    }
    order = sample(list(half_b), size=len(half_b))
    return [half_b[t] for t in order[SAMPLE : SAMPLE + NEW_TASKS]]


def new() -> None:  # pragma: no cover - network and local API
    from app.mcp_server.client import NinaApiClient
    from bench.tasks_schema import load_external_tasks

    tasks = {t.id: t for t in load_external_tasks(Path("bench/tasks/external.yaml"))}
    api = NinaApiClient(base_url="http://127.0.0.1:8000", api_key="nina_local_dev")
    api.set_caller("teste-context7")
    folder = _OUT / "new"
    folder.mkdir(parents=True, exist_ok=True)
    keys = _new_keys()
    for done, key in enumerate(keys):
        path = folder / f"{key['task_id']}.json"
        replies = json.loads(path.read_text("utf-8")) if path.exists() else {}
        question = tasks[key["task_id"]].question_title
        url = urldefrag(key["url"])[0]
        if "nina" not in replies:
            replies["nina"] = _nina(api, question, url, FINAL_BUDGET)
        if "c7_fetch" not in replies:
            replies["c7_fetch"] = _context7("/v3/fetch", url=url, query=question)
        path.write_text(json.dumps(replies, ensure_ascii=False), encoding="utf-8")
        left = replies["c7_fetch"].get("left")
        print(f"{done + 1}/{len(keys)} quota left {left}", flush=True)


def _final_rows(fresh: bool) -> list[dict[str, Any]]:  # pragma: no cover - local data
    """The 80 seen tasks at the final budget, or the new tasks."""
    from bench.tasks_schema import load_external_tasks

    tasks = {t.id: t for t in load_external_tasks(Path("bench/tasks/external.yaml"))}
    rows = []
    for key in _new_keys() if fresh else _keys():
        name = f"{key['task_id']}.json"
        folder = _OUT / "new" if fresh else _OUT
        raw = json.loads((folder / name).read_text("utf-8"))
        if not fresh:
            lean = (_OUT / f"budget_{FINAL_BUDGET}" / name).read_text("utf-8")
            raw["nina"] = json.loads(lean)
        section = units(key["section"])
        task = tasks[key["task_id"]]
        kept = [a for a in ARMS if a in raw]
        rows.append(
            {
                "task": key["task_id"],
                "site": key["site"],
                "question": task.question_title,
                "reference": task.reference_answer,
                "raw": raw,
                "reply": {a: raw[a].get("text", "") for a in kept},
                "hit": {a: soft_hit(section, raw[a].get("text", "")) for a in kept},
            }
        )
    return rows


def sheet2() -> None:  # pragma: no cover - local data
    """One shuffled blind sheet for both task sets, split into small batches."""
    folder = _OUT.parent / "context7_final"
    folder.mkdir(exist_ok=True)
    cases, key = blind_sheet(_final_rows(False) + _final_rows(True), seed=11)
    random.Random(11).shuffle(cases)
    (folder / "key.json").write_text(json.dumps(key), encoding="utf-8")
    batch: list[dict[str, Any]] = []
    size = number = 0
    for case in cases:
        clean = {**case, "A": scrub(case["A"]), "B": scrub(case["B"])}
        for label in "AB":  # scrubbing must never empty a reply with page text
            if len(case[label]) > 300 and len(clean[label]) < 100:
                raise SystemExit(f"scrub emptied {case['case']}")
        batch.append(clean)
        size += len(clean["A"]) + len(clean["B"]) + len(clean["reference_answer"])
        if size > 60_000:
            path = folder / f"batch_{number}.json"
            path.write_text(json.dumps(batch, ensure_ascii=False), encoding="utf-8")
            batch, size, number = [], 0, number + 1
    if batch:
        path = folder / f"batch_{number}.json"
        path.write_text(json.dumps(batch, ensure_ascii=False), encoding="utf-8")
        number += 1
    print(f"{len(cases)} blind cases in {number} batches")


def final() -> None:  # pragma: no cover - local data
    from app.index.tokens import count_tokens

    folder = _OUT.parent / "context7_final"
    key = json.loads((folder / "key.json").read_text("utf-8"))
    decisions: dict[str, dict[str, bool]] = {}
    text = (folder / "decisions.txt").read_text("utf-8")
    for code, a, b in re.findall(r"(\w+) A=(yes|no) B=(yes|no)", text):
        decisions[code] = {"A": a == "yes", "B": b == "yes"}
    if len(decisions) != len(key):
        raise SystemExit(f"{len(decisions)} decisions for {len(key)} cases")
    out = [f"# Final check, budget {FINAL_BUDGET}, {len(key)} blind decisions", ""]
    groups = [("80 seen", False, RIVALS), (f"{NEW_TASKS} new", True, ("c7_fetch",))]
    for name, fresh, rivals in groups:
        rows = _final_rows(fresh)
        for rival in rivals:
            kept = [r for r in rows if rival in r["raw"]]
            pairs = [adjusted(r, rival, decisions, key) for r in kept]
            result = paired([p[0] for p in pairs], [p[1] for p in pairs])
            ours = statistics.mean(count_tokens(r["reply"]["nina"]) for r in kept)
            theirs = statistics.mean(count_tokens(r["reply"][rival]) for r in kept)
            refused = sum(1 for r in kept if r["raw"][rival]["status"] != 200)
            share = ours / theirs
            found = statistics.mean(p[0] for p in pairs)
            other = statistics.mean(p[1] for p in pairs)
            out += [
                f"## {name}: nina vs {rival} ({len(kept)} tasks)",
                f"- hit: nina {found:.3f}, {rival} {other:.3f}",
                f"- d {result['diff']:+.3f}, "
                f"CI [{result['lo']:+.3f}, {result['hi']:+.3f}], "
                f"only nina {result['only_a']}, only rival {result['only_b']}",
                f"- tokens: nina {ours:,.0f}, {rival} {theirs:,.0f}, share {share:.2f}",
                f"- {rival} refused or failed: {refused}",
                f"- **{verdict(result['diff'], result['lo'], result['hi'], share)}**",
                "",
            ]
    report_text = "\n".join(out)
    (folder / "verdict.md").write_text(report_text, encoding="utf-8")
    print(report_text)


def _rows() -> list[dict[str, Any]]:  # pragma: no cover - local data
    from bench.tasks_schema import load_external_tasks

    tasks = {t.id: t for t in load_external_tasks(Path("bench/tasks/external.yaml"))}
    rows = []
    for key in _keys():
        path = _OUT / f"{key['task_id']}.json"
        replies = json.loads(path.read_text("utf-8"))
        section = units(key["section"])
        task = tasks[key["task_id"]]
        rows.append(
            {
                "task": key["task_id"],
                "site": key["site"],
                "question": task.question_title,
                "reference": task.reference_answer,
                "raw": replies,
                "reply": {a: replies.get(a, {}).get("text", "") for a in ARMS},
                "hit": {
                    a: soft_hit(section, replies.get(a, {}).get("text", ""))
                    for a in ARMS
                },
            }
        )
    return rows


def sheet() -> None:  # pragma: no cover - local data
    cases, key = blind_sheet(_rows())
    (_OUT / "blind_sheet.json").write_text(
        json.dumps(cases, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    (_OUT / "blind_key.json").write_text(json.dumps(key), encoding="utf-8")
    print(f"{len(cases)} blind cases")


def report() -> None:  # pragma: no cover - local data
    from app.index.tokens import count_tokens

    rows = _rows()
    key = json.loads((_OUT / "blind_key.json").read_text("utf-8"))
    decided = _OUT / "blind_decisions.json"
    decisions = json.loads(decided.read_text("utf-8")) if decided.exists() else {}
    tokens = {
        arm: [
            count_tokens(r["reply"][arm])
            for r in rows
            if arm == "nina" or arm in r["raw"]
        ]
        for arm in ARMS
    }
    lines = [
        f"# NINA vs Context7, half B, {len(rows)} tasks, "
        f"{len(decisions)} blind decisions",
        "",
        "| arm | soft hit | mean tokens | median tokens | median s | failed |",
        "|---|---|---|---|---|---|",
    ]
    for arm in ARMS:
        cells = [r["raw"][arm] for r in rows if arm in r["raw"]]
        hits = [r["hit"][arm] for r in rows if arm in r["raw"]]
        mean_tokens = statistics.mean(tokens[arm])
        lines.append(
            f"| {arm} | {statistics.mean(hits):.3f} | {mean_tokens:,.0f} "
            f"| {statistics.median(tokens[arm]):,.0f} "
            f"| {statistics.median(c['seconds'] for c in cells):.1f} "
            f"| {sum(1 for c in cells if c['status'] != 200)} |"
        )
    lines.append("")
    for rival in RIVALS:
        kept = [r for r in rows if rival in r["raw"]]
        pairs = [adjusted(r, rival, decisions, key) for r in kept]
        result = paired([p[0] for p in pairs], [p[1] for p in pairs])
        nina_tokens = statistics.mean(count_tokens(r["reply"]["nina"]) for r in kept)
        rival_tokens = statistics.mean(count_tokens(r["reply"][rival]) for r in kept)
        share = nina_tokens / rival_tokens
        nina_hit = statistics.mean(p[0] for p in pairs)
        rival_hit = statistics.mean(p[1] for p in pairs)
        lines += [
            f"## nina vs {rival} ({len(kept)} tasks)",
            f"- hit after blind review: nina {nina_hit:.3f}, {rival} {rival_hit:.3f}",
            f"- d {result['diff']:+.3f}, "
            f"CI [{result['lo']:+.3f}, {result['hi']:+.3f}], "
            f"only nina {result['only_a']}, only {rival} {result['only_b']}",
            f"- tokens: nina {nina_tokens:,.0f}, {rival} {rival_tokens:,.0f}, "
            f"share {share:.2f}",
            f"- **{verdict(result['diff'], result['lo'], result['hi'], share)}**",
            "",
        ]
    text = "\n".join(lines)
    (_OUT.parent / "context7_verdict.md").write_text(text, encoding="utf-8")
    print(text)


def main() -> None:  # pragma: no cover - dispatch
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "step",
        choices=["run", "sheet", "verdict", "sweep", "new", "sheet2", "final"],
    )
    steps = {
        "run": run,
        "sheet": sheet,
        "verdict": report,
        "sweep": sweep,
        "new": new,
        "sheet2": sheet2,
        "final": final,
    }
    steps[parser.parse_args().step]()


if __name__ == "__main__":
    main()
