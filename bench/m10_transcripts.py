"""Read finished M10 transcripts for the post-run exploratory checks.

:mod:`bench.m10_agent` writes every SDK message of a session attempt to
``transcripts/<task>__<arm>__<attempt>.jsonl``. This module gives, per (task,
arm), the last attempt's tool results and final message, and the word n-gram
helpers that :mod:`bench.m10_contamination` and :mod:`bench.m10_context_use`
share. It only reads files the run already kept (PREREG §11); it is for after
the run, never for monitoring (PREREG §10 point 4).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path

_WORD = re.compile(r"\w+")


@dataclass
class Session:
    """The last attempt of one (task, arm) session."""

    task_id: str
    arm: str
    #: (tool name, result text), in order.
    tool_results: list[tuple[str, str]] = field(default_factory=list)
    final_text: str = ""


def _text(content: object) -> str:
    if isinstance(content, list):
        return "".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
    return content if isinstance(content, str) else ""


def parse_session(task_id: str, arm: str, lines: Iterable[str]) -> Session:
    """Tool results and the final message (the result message's text)."""
    session = Session(task_id, arm)
    names: dict[str, str] = {}
    for line in lines:
        if not line.strip():
            continue
        msg = json.loads(line)
        kind = msg.get("type")
        if kind == "ResultMessage":
            session.final_text = str(msg.get("result") or "")
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if kind == "AssistantMessage" and "name" in block:
                names[str(block.get("id"))] = str(block["name"])
            elif kind == "UserMessage" and "tool_use_id" in block:
                name = names.get(str(block["tool_use_id"]), "")
                session.tool_results.append((name, _text(block.get("content"))))
    return session


def last_sessions(folder: Path) -> Iterator[Session]:
    """Each (task, arm)'s last attempt; earlier ones were infrastructure retries."""
    last: dict[tuple[str, str], tuple[int, Path]] = {}
    for path in folder.glob("*.jsonl"):
        task, arm, attempt = path.stem.rsplit("__", 2)
        if (task, arm) not in last or int(attempt) > last[(task, arm)][0]:
            last[(task, arm)] = (int(attempt), path)
    for (task, arm), (_n, path) in sorted(last.items()):
        with path.open(encoding="utf-8") as fh:
            yield parse_session(task, arm, fh)


def words(text: str) -> list[str]:
    """Lower-cased word tokens (letters, digits, underscore)."""
    return _WORD.findall(text.lower())


def ngrams(tokens: Sequence[str], n: int) -> set[tuple[str, ...]]:
    """The set of word n-grams of ``tokens``."""
    return {tuple(tokens[i : i + n]) for i in range(len(tokens) - n + 1)}
