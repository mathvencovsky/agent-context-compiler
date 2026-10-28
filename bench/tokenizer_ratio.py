"""How many Claude tokens one NINA budget token costs (zero-cost research item 3).

NINA counts its budget with tiktoken ``cl100k_base`` (``app/index/tokens.py``),
but the M10 agent is Claude, billed by Claude's own tokenizer. Third-party
measurements put Claude at 1.3x to 1.7x the GPT count on code. If that holds
for documentation fragments, ``max_tokens=4000`` delivers well over 4,000
billed tokens, and the budget the API promises is not the one the user pays.

This script samples chunks of the frozen pages cited by ``dev`` tasks, exactly
as NINA's chunker cuts them (offline extraction, no provider), and counts each
one twice: with ``cl100k_base`` and with Anthropic's token-counting endpoint,
which is free (rate-limited per minute, not billed). It reports, per site and
for code vs prose, the ratio of the totals and the median per-chunk ratio.

It also counts the NINA MCP tool definitions the way Claude bills them (the
endpoint's ``tools`` field): they are sent on every agent turn, and the O2 test
only caps them in ``cl100k_base`` tokens (800).

Only the ``dev`` split is read (PREREG Amendment 6); no outcome is involved.

Usage (needs the corpus and an Anthropic key; no paid call)::

    uv run python -m bench.tokenizer_ratio [--per-site 40] [--data-root DIR]

Output: ``bench/results/m10a/tokenizer_ratio.md``.
"""

from __future__ import annotations

import argparse
import random
import statistics
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from app.index.tokens import count_tokens

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT = REPO_ROOT / "bench" / "results" / "m10a" / "tokenizer_ratio.md"
SEED = 20261028
#: The M10 agent model; its tokenizer is the one billed.
MODEL = "claude-sonnet-5-5"


@dataclass(frozen=True)
class Sample:
    """One chunk's Markdown, where it came from, and both counts."""

    site: str
    kind: str  # "code" (has a fenced block) or "prose"
    cl100k: int
    claude: int


def kind_of(markdown: str) -> str:
    return "code" if "```" in markdown else "prose"


def pick(
    chunks_by_site: dict[str, list[str]], per_site: int, seed: int = SEED
) -> list[tuple[str, str]]:
    """Up to ``per_site`` chunks per site, seeded, as ``(site, markdown)``."""
    rng = random.Random(seed)
    picked = []
    for site in sorted(chunks_by_site):
        chunks = [c for c in chunks_by_site[site] if c.strip()]
        for md in rng.sample(chunks, min(per_site, len(chunks))):
            picked.append((site, md))
    return picked


def measure(
    picked: Iterable[tuple[str, str]], claude_count: Callable[[str], int]
) -> list[Sample]:
    """Both counts per chunk; ``claude_count`` already removes message overhead."""
    return [
        Sample(site, kind_of(md), count_tokens(md), claude_count(md))
        for site, md in picked
    ]


def table(samples: Sequence[Sample]) -> str:
    """Markdown report: ratio of totals and median per-chunk ratio per group."""
    groups: dict[str, list[Sample]] = defaultdict(list)
    for s in samples:
        groups["all"].append(s)
        groups[f"kind: {s.kind}"].append(s)
        groups[f"site: {s.site}"].append(s)
    lines = [
        "# Claude tokens per cl100k token (NINA fragments, dev pages)",
        "",
        "| group | chunks | cl100k | Claude | ratio of totals | median ratio |",
        "|---|---|---|---|---|---|",
    ]
    order = ["all", "kind: code", "kind: prose"]
    keys = [k for k in order if k in groups] + sorted(
        k for k in groups if k not in order
    )
    for key in keys:
        rows = groups[key]
        cl = sum(r.cl100k for r in rows)
        cc = sum(r.claude for r in rows)
        med = statistics.median(r.claude / r.cl100k for r in rows if r.cl100k)
        lines.append(
            f"| {key} | {len(rows)} | {cl} | {cc} | {cc / max(cl, 1):.3f} | {med:.3f} |"
        )
    ratio = sum(r.claude for r in samples) / max(sum(r.cl100k for r in samples), 1)
    lines += [
        "",
        f"A `max_tokens=4000` budget is about {round(4000 * ratio)} Claude tokens.",
    ]
    return "\n".join(lines) + "\n"


def anthropic_counter(api_key: str, model: str = MODEL) -> Callable[..., int]:
    """Count with the free endpoint, minus the fixed cost of an empty message.

    The endpoint counts a whole user message. Its wrapper costs a fixed number
    of tokens, measured once as the count of a one-character message minus 1.
    """
    import anthropic

    client = anthropic.Anthropic(api_key=api_key, max_retries=8)

    def raw(text: str, tools: list[dict[str, object]] | None = None) -> int:
        extra = {"tools": tools} if tools else {}
        reply = client.messages.count_tokens(
            model=model, messages=[{"role": "user", "content": text}], **extra
        )
        return int(reply.input_tokens)

    overhead = raw("x") - 1

    def count(text: str, tools: list[dict[str, object]] | None = None) -> int:
        """Tokens of ``text``; with ``tools``, the tools' cost alone."""
        if tools:
            return raw("x", tools) - raw("x")
        return raw(text) - overhead

    return count


def nina_tools() -> list[dict[str, object]]:
    """The NINA MCP tools as the Messages API receives them."""
    import asyncio

    from app.mcp_server.client import NinaApiClient
    from app.mcp_server.server import build_server

    server = build_server(
        client=NinaApiClient(base_url="http://nina.local", api_key="x")
    )
    tools = asyncio.run(server.list_tools())
    return [
        {
            "name": t.name,
            "description": t.description or "",
            "input_schema": t.input_schema,
        }
        for t in tools
    ]


def tools_line(cl100k: int, claude: int) -> str:
    return (
        f"NINA MCP tool definitions, sent on every turn: {cl100k} cl100k tokens, "
        f"{claude} Claude tokens."
    )


def dev_chunks(data_root: Path) -> dict[str, list[str]]:  # pragma: no cover - corpus
    """Chunk Markdown of every frozen page cited by a ``dev`` task, per site."""
    from app.index.chunker import chunk_document
    from bench.corpus_store import CorpusStore
    from bench.runners.ours import _extract_ir, _source_for
    from bench.tasks_schema import load_external_tasks

    store = CorpusStore(data_root / "bench" / "corpus")
    tasks = load_external_tasks(REPO_ROOT / "bench/tasks/external.yaml")
    urls = {(t.site, u) for t in tasks if t.split == "dev" for u in t.cited_urls}
    out: dict[str, list[str]] = defaultdict(list)
    for site, url in sorted(urls):
        try:
            raw = store.raw_bytes(url)
            content_type = store.content_type(url)
        except (KeyError, ValueError):
            continue
        source, _, _ = _source_for(url, content_type)
        doc = _extract_ir(raw, content_type, source)
        out[site].extend(c.md for c in chunk_document(doc))
    return out


def main(argv: list[str] | None = None) -> None:  # pragma: no cover - API call
    import os

    from dotenv import dotenv_values

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--per-site", type=int, default=40)
    parser.add_argument("--data-root", type=Path, default=REPO_ROOT)
    args = parser.parse_args(argv)
    env = {**dotenv_values(args.data_root / ".env"), **os.environ}
    key = env.get("M10_AGENT_API_KEY") or env.get("BENCH_JUDGE_API_KEY")
    if not key:
        raise SystemExit("set M10_AGENT_API_KEY or BENCH_JUDGE_API_KEY (Anthropic)")
    import json

    counter = anthropic_counter(key)
    picked = pick(dev_chunks(args.data_root), args.per_site)
    tools = nina_tools()
    report = (
        table(measure(picked, counter))
        + "\n"
        + tools_line(count_tokens(json.dumps(tools)), counter("", tools))
        + "\n"
    )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
