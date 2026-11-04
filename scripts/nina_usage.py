"""Real NINA use, read from Claude Code's own session history (~/.claude/projects).

Every NINA MCP call becomes one row: tokens NINA delivered, tokens of the whole
page(s), the share cut from the whole page, and time. That share is not savings:
Claude Code's own reader does not hand the whole page to the model, and it cost
less than NINA on 5 real questions (DECISIONS 2026-11-04). Writes a JSON array for
the "NINA na vida real" panel. Nothing in NINA or Claude Code is changed.

    uv run python -m scripts.nina_usage out.json
    uv run python -m scripts.nina_usage --api out.json
    uv run python -m scripts.nina_usage --tasks out.json [--since 2026-11-05]
    uv run python -m scripts.nina_usage --selfcheck

``--api`` reads instead what NINA recorded itself (GET /v1/usage, local use
recorded since DECISIONS 2026-11-02): every client, per day and endpoint, with
no question text.

``--tasks`` gives one row per task (a prompt and the work it caused, subagents
included) that read or searched the web: did it call NINA without the prompt
naming it, did it also use the native reader, was anything refused, how big were
the pages. No prompt text is kept. NINA's own development and test runs stay out
of the summary; the Claude app's chat does not write this history at all.
"""

from __future__ import annotations

import json
import re
import statistics
import sys
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from app.index.tokens import count_tokens

HISTORY = Path.home() / ".claude" / "projects"
USAGE_URL = "http://127.0.0.1:8000/v1/usage"
PREFIX = "mcp__nina__"
_HEADER = re.compile(
    r"coverage: (\w+) \| (\d+)/\d+ tokens \| sources: (\d+) ok, (\d+) failed"
)
# shortcut: the concise reply rounds page size to 2 digits and omits pages that gave
# no fragment, so savings are slightly understated; exact with response_format=detailed.
_PAGE = re.compile(r'excerpt="[^"]*~(\d+)-token page"')


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(c.get("text", "") for c in content or [] if isinstance(c, dict))


def _when(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone()


def parse_reply(tool: str, text: str) -> tuple[int, int, str | None, int, int] | None:
    """(tokens_nina, tokens_pagina, coverage, ok, failed); None for an error."""
    if tool != "assemble_context":
        n = count_tokens(text)  # resolve_url / get_document hand over the whole page
        return n, n, None, 1, 0
    if text.lstrip().startswith("{"):  # response_format="detailed"
        body = json.loads(text)
        sources = body.get("sources", [])
        ok = [s for s in sources if s.get("status") == "ok"]
        pages = sum(s.get("page_tokens") or 0 for s in ok)
        return (
            body["token_usage"]["output_tokens"],
            pages,
            body.get("coverage"),
            len(ok),
            len(sources) - len(ok),
        )
    m = _HEADER.search(text)
    if m is None:
        return None
    pages = sum(int(n) for n in _PAGE.findall(text))
    return int(m[2]), pages, m[1], int(m[3]), int(m[4])


def _entries(path: Path, needle: str) -> Any:
    with path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            if needle in line:
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue


def _blocks(entry: dict[str, Any]) -> list[dict[str, Any]]:
    content = entry.get("message", {}).get("content")
    return (
        [c for c in content if isinstance(c, dict)] if isinstance(content, list) else []
    )


def collect() -> list[dict[str, Any]]:
    files = list(HISTORY.rglob("*.jsonl"))
    uses: dict[str, tuple[datetime, str, dict[str, Any], str]] = {}
    for path in files:
        for e in _entries(path, PREFIX):
            for c in _blocks(e):
                if (
                    c.get("type") == "tool_use"
                    and str(c.get("name", "")).startswith(PREFIX)
                    and e.get("timestamp")
                ):
                    uses[c["id"]] = (
                        _when(e["timestamp"]),
                        c["name"][len(PREFIX) :],
                        c.get("input", {}),
                        e.get("cwd", ""),
                    )
    rows: dict[str, dict[str, Any]] = {}
    for path in files:
        for e in _entries(path, '"tool_result"'):
            for c in _blocks(e):
                tid = c.get("tool_use_id")
                if (
                    c.get("type") != "tool_result"
                    or tid not in uses
                    or tid in rows
                    or not e.get("timestamp")
                ):
                    continue
                started, tool, args, cwd = uses[tid]
                text = _text(c.get("content"))
                urls = args.get("urls") or [args.get("url", "")]
                row: dict[str, Any] = {
                    "id": tid,
                    "quando": started.isoformat(timespec="seconds"),
                    "dia": started.date().isoformat(),
                    "ferramenta": tool,
                    "projeto": Path(cwd).name,
                    "pergunta": args.get("query")
                    or args.get("url")
                    or args.get("document_id", ""),
                    "sites": ", ".join(sorted({urlsplit(u).netloc for u in urls if u})),
                    "segundos": round(
                        (_when(e["timestamp"]) - started).total_seconds(), 1
                    ),
                    "status": "erro",
                    "motivo": "",
                }
                parsed = None if c.get("is_error") else parse_reply(tool, text)
                if parsed is None:
                    row["motivo"] = (
                        text.strip().splitlines()[0][:200]
                        if text.strip()
                        else "sem resposta"
                    )
                else:
                    nina, page, coverage, ok, failed = parsed
                    row.update(
                        status="ok",
                        tokens_nina=nina,
                        tokens_pagina=page,
                        economia_pct=round(100 * (1 - nina / page), 1)
                        if page
                        else None,
                        cobertura=coverage,
                        fontes_ok=ok,
                        fontes_falha=failed,
                    )
                rows[tid] = row
    return sorted(rows.values(), key=lambda r: r["quando"])


def from_api(body: dict[str, Any], made: str) -> list[dict[str, Any]]:
    """One row per (day, endpoint) of a GET /v1/usage body, leaving out /v1/usage."""
    return [
        {
            "dia": day["day"],
            "endpoint": e["endpoint"],
            "requisicoes": e["requests"],
            "tokens_entregues": e["tokens_delivered"],
            "tokens_paginas": e["page_tokens"],
            "p50_ms": e["latency_p50_ms"],
            "gerado": made,
        }
        for day in body["by_day"]
        for e in day["endpoints"]
        if e["endpoint"] != "/v1/usage"
    ]


def _native_read(name: str, args: dict[str, Any]) -> bool:
    return name == "WebFetch" or (
        name == "Agent" and args.get("subagent_type") == "web-fetch"
    )


def _prompt_text(entry: dict[str, Any]) -> str:
    """The words the user typed in this entry, or "" for anything else."""
    if entry.get("isMeta") or entry.get("isCompactSummary") or entry.get("agentId"):
        return ""
    content = entry.get("message", {}).get("content")
    if isinstance(content, str):
        return content
    return "\n".join(
        c.get("text", "") for c in _blocks(entry) if c.get("type") == "text"
    )


def task_rows(entries: Any) -> list[dict[str, Any]]:
    """One row per task with web activity, from history entries in any order."""
    calls: dict[str, tuple[str, dict[str, Any]]] = {}
    prompts: list[dict[str, Any]] = []
    for entry in entries:
        if entry.get("type") == "assistant":
            for c in _blocks(entry):
                if c.get("type") == "tool_use":
                    calls[c["id"]] = (str(c.get("name", "")), c.get("input", {}))
        elif entry.get("type") == "user" and entry.get("promptId"):
            prompts.append(entry)
    tasks: dict[tuple[str, str], dict[str, Any]] = {}
    for entry in prompts:
        key = (str(entry.get("sessionId", "")), entry["promptId"])
        task = tasks.setdefault(
            key,
            {
                "named": False,
                "when": None,
                "cwd": "",
                "entrypoint": None,
                "nina": {},
                "native": set(),
                "search": set(),
                "refused": set(),
                "pages": [],
            },
        )
        if entry.get("entrypoint"):
            task["entrypoint"] = entry["entrypoint"]
        if not entry.get("agentId") and entry.get("cwd"):
            task["cwd"] = entry["cwd"]
        typed = _prompt_text(entry)
        if typed:
            task["named"] = task["named"] or "nina" in typed.lower()
            if task["when"] is None and entry.get("timestamp"):
                task["when"] = _when(entry["timestamp"])
        for c in _blocks(entry):
            tid = c.get("tool_use_id")
            if c.get("type") != "tool_result" or tid not in calls:
                continue
            name, args = calls[tid]
            if name.startswith(PREFIX):
                text = _text(c.get("content"))
                parsed = (
                    None
                    if c.get("is_error")
                    else parse_reply(name[len(PREFIX) :], text)
                )
                task["nina"][tid] = name
                if parsed is None or parsed[4] > 0:
                    task["refused"].add(tid)
                if parsed is not None and parsed[1]:
                    task["pages"].append(parsed[1])
            elif _native_read(name, args):
                task["native"].add(tid)
            elif name == "WebSearch":
                task["search"].add(tid)
    rows = []
    for (session, _), task in tasks.items():
        if not (task["nina"] or task["native"] or task["search"]):
            continue
        when = task["when"]
        rows.append(
            {
                "sessao": session,
                "quando": when.isoformat(timespec="seconds") if when else "",
                "dia": when.date().isoformat() if when else "",
                "projeto": task["cwd"],
                "entrada": task["entrypoint"],
                "pediu_nina": task["named"],
                "nina": len(task["nina"]),
                "nativo": len(task["native"]),
                "buscas": len(task["search"]),
                "recusas": len(task["refused"]),
                "maior_pagina": max(task["pages"], default=0),
            }
        )
    return sorted(rows, key=lambda r: r["quando"])


def is_real_use(row: dict[str, Any]) -> bool:
    """Neither NINA's own development nor a test run."""
    where = row["projeto"].replace("\\", "/").lower()
    entry = row["entrada"] or ""
    return not (
        entry.startswith("sdk") or "/downloads/nina" in where or "/temp/" in where
    )


def summary(rows: list[dict[str, Any]]) -> str:
    """What the exit rule of the plan asks of real use."""
    reads = [r for r in rows if r["nina"] or r["nativo"]]
    alone = [r for r in reads if r["nina"] and not r["pediu_nina"]]
    pages = [r["maior_pagina"] for r in reads if r["maior_pagina"]]
    share = f"{len(alone) / len(reads):.0%}" if reads else "n/a"
    both = sum(1 for r in reads if r["nina"] and r["nativo"])
    median = statistics.median(pages) if pages else "n/a"
    return "\n".join(
        [
            f"tarefas que leram alguma pagina: {len(reads)}",
            f"chamaram o NINA sem ser pedido: {len(alone)} ({share})",
            f"NINA e leitor nativo na mesma tarefa: {both}",
            f"so leitor nativo: {sum(1 for r in reads if not r['nina'])}",
            f"com recusa do NINA: {sum(1 for r in reads if r['recusas'])}",
            f"maior pagina, mediana: {median} tokens",
        ]
    )


def _all_entries() -> Any:
    for path in HISTORY.rglob("*.jsonl"):
        yield from _entries(path, '"type":"')


def rows_of(user: Any, tool: Any, result: Any, sample: str) -> list[dict[str, Any]]:
    """Four tasks: NINA alone; named, with a refusal and WebFetch; subagent; search."""
    return task_rows(
        [
            tool("1", "mcp__nina__assemble_context"),
            tool("2", "mcp__nina__assemble_context"),
            tool("3", "WebFetch"),
            tool("4", "Agent", subagent_type="web-fetch"),
            tool("5", "WebSearch"),
            tool("6", "Read"),
            user("a", "how do I install it?"),
            user("a", [result("1", sample)]),
            user("a", [result("6")]),
            user("b", "use NINA for this"),
            user("b", [result("2", "error: all_sources_failed")]),
            user("b", [result("3")]),
            user("c", "read that page"),
            user("c", [result("4")]),
            user("d", "search for it"),
            user("d", [result("5")]),
            user("e", "no web here"),
        ]
    )


def _selfcheck() -> None:
    sample = (
        "coverage: sufficient | 980/1000 tokens | sources: 1 ok, 0 failed\n"
        '<document source="https://x.org/a" id="d" version="v" '
        'excerpt="11 fragments of a ~5600-token page">'
    )
    assert parse_reply("assemble_context", sample) == (980, 5600, "sufficient", 1, 0)
    assert parse_reply("assemble_context", "error: all_sources_failed") is None
    assert parse_reply("resolve_url", "hello world")[:2] == (2, 2)  # type: ignore[index]
    endpoint = {
        "requests": 1,
        "tokens_delivered": 9,
        "page_tokens": 90,
        "latency_p50_ms": 5,
    }
    body = {
        "by_day": [
            {
                "day": "2026-11-02",
                "endpoints": [
                    {**endpoint, "endpoint": "/v1/context/assemble"},
                    {**endpoint, "endpoint": "/v1/usage"},
                ],
            }
        ]
    }
    (row,) = from_api(body, "t")
    assert (row["endpoint"], row["tokens_paginas"]) == ("/v1/context/assemble", 90)

    def user(prompt: str, content: Any, **extra: Any) -> dict[str, Any]:
        return {
            "type": "user",
            "sessionId": "s",
            "promptId": prompt,
            "timestamp": "2026-11-05T10:00:00Z",
            "cwd": "C:/work/app",
            "message": {"content": content},
            **extra,
        }

    def tool(tid: str, name: str, **args: Any) -> dict[str, Any]:
        block = {"type": "tool_use", "id": tid, "name": name, "input": args}
        return {"type": "assistant", "message": {"content": [block]}}

    def result(tid: str, text: str = "x") -> dict[str, Any]:
        return {"type": "tool_result", "tool_use_id": tid, "content": text}

    a, b, c, d = rows_of(user, tool, result, sample)
    assert (a["nina"], a["pediu_nina"], a["maior_pagina"]) == (1, False, 5600)
    assert (b["pediu_nina"], b["nativo"], b["recusas"]) == (True, 1, 1)
    assert (c["nativo"], c["nina"]) == (1, 0)  # a web-fetch subagent is a native read
    assert (d["buscas"], d["nina"], d["nativo"]) == (1, 0, 0)
    assert "tarefas que leram alguma pagina: 3" in summary([a, b, c, d])
    assert "chamaram o NINA sem ser pedido: 1 (33%)" in summary([a, b, c, d])
    assert is_real_use(a) and not is_real_use({**a, "entrada": "sdk-cli"})
    assert not is_real_use({**a, "projeto": "C:\\Users\\M\\Downloads\\Nina"})
    print("selfcheck ok")


if __name__ == "__main__":
    if sys.argv[1:] == ["--selfcheck"]:
        _selfcheck()
    elif sys.argv[1] == "--tasks":
        since = sys.argv[sys.argv.index("--since") + 1] if "--since" in sys.argv else ""
        found = [r for r in task_rows(_all_entries()) if r["dia"] >= since]
        Path(sys.argv[2]).write_text(
            json.dumps(found, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        print(summary([r for r in found if is_real_use(r)]))
    elif sys.argv[1] == "--api":
        with urllib.request.urlopen(USAGE_URL, timeout=30) as response:
            usage = json.load(response)
        made = datetime.now().astimezone().isoformat(timespec="seconds")
        found = from_api(usage, made)
        Path(sys.argv[2]).write_text(
            json.dumps(found, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        print(f"{len(found)} day/endpoint rows -> {sys.argv[2]}")
    else:
        found = collect()
        Path(sys.argv[1]).write_text(
            json.dumps(found, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        print(f"{len(found)} NINA calls -> {sys.argv[1]}")
