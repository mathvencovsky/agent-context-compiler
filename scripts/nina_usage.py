"""Real NINA use, read from Claude Code's own session history (~/.claude/projects).

Every NINA MCP call becomes one row: tokens NINA delivered, tokens of the whole
page(s) the agent would read without it, savings and time. Writes a JSON array
for the "NINA na vida real" panel. Nothing in NINA or Claude Code is changed.

    uv run python scripts/nina_usage.py out.json
    uv run python scripts/nina_usage.py --selfcheck
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from app.index.tokens import count_tokens

HISTORY = Path.home() / ".claude" / "projects"
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


def _selfcheck() -> None:
    sample = (
        "coverage: sufficient | 980/1000 tokens | sources: 1 ok, 0 failed\n"
        '<document source="https://x.org/a" id="d" version="v" '
        'excerpt="11 fragments of a ~5600-token page">'
    )
    assert parse_reply("assemble_context", sample) == (980, 5600, "sufficient", 1, 0)
    assert parse_reply("assemble_context", "error: all_sources_failed") is None
    assert parse_reply("resolve_url", "hello world")[:2] == (2, 2)  # type: ignore[index]
    print("selfcheck ok")


if __name__ == "__main__":
    if sys.argv[1:] == ["--selfcheck"]:
        _selfcheck()
    else:
        found = collect()
        Path(sys.argv[1]).write_text(
            json.dumps(found, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        print(f"{len(found)} NINA calls -> {sys.argv[1]}")
