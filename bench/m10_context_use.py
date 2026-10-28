"""How much of the tool context each arm paid for reaches the answer (L9, L10).

Exploratory, from the transcripts the run already keeps (PREREG §11), after
the run. NINA's thesis is "the same success with less context", so the most
direct measure is the share of tool-result text that the agent actually used
(ContextBench reports a "substantial gap between explored and utilized
context"). Per session, deterministically:

* ``used_words``: words of the tool results covered by a word
  :data:`USE_N`-gram that also appears in the final message (reasoning, answer
  and citation quotes), out of ``tool_words``;
* ``quoted_tokens``: tokens of the citation quotes found verbatim (case and
  whitespace folded) in some tool result, out of ``tool_tokens``; PREREG §4
  asks for verbatim quotes;
* ``tool_calls`` and ``json_missing``: a final message without the
  ``{"answer", "citations"}`` object is judged whole (PREREG §4), and long
  answers may be judged differently, so the rate per arm is reported (L10).

Per arm: token-weighted shares, tool calls per correct task (BrowseComp-Plus:
better retrieval also shows as fewer calls) and the used share among correct
and incorrect sessions.

Usage::

    uv run python -m bench.m10_context_use --out bench/results/m10
"""

from __future__ import annotations

import argparse
import csv
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from app.index.tokens import count_tokens
from bench.judge_external import final_answer
from bench.m10_transcripts import Session, last_sessions, ngrams, words

#: Word n-gram length that counts as "used" (short enough for code and names).
USE_N = 5
_SPACE = re.compile(r"\s+")


def _fold(text: str) -> str:
    return _SPACE.sub(" ", text).strip().lower()


@dataclass(frozen=True)
class SessionUse:
    task_id: str
    arm: str
    tool_calls: int
    tool_tokens: int
    tool_words: int
    used_words: int
    quoted_tokens: int
    json_missing: bool


def _covered(tokens: list[str], grams: set[tuple[str, ...]]) -> int:
    hit = [False] * len(tokens)
    for i in range(len(tokens) - USE_N + 1):
        if tuple(tokens[i : i + USE_N]) in grams:
            for j in range(i, i + USE_N):
                hit[j] = True
    return sum(hit)


def session_use(session: Session) -> SessionUse:
    final = session.final_text
    parsed = final_answer(final)
    grams = ngrams(words(final), USE_N)
    texts = [text for _name, text in session.tool_results]
    folded = [_fold(t) for t in texts]
    quoted = sum(
        count_tokens(c.quote)
        for c in parsed.citations
        if c.quote.strip() and any(_fold(c.quote) in f for f in folded)
    )
    tool_words = used = 0
    for text in texts:
        tokens = words(text)
        tool_words += len(tokens)
        used += _covered(tokens, grams)
    return SessionUse(
        task_id=session.task_id,
        arm=session.arm,
        tool_calls=len(texts),
        tool_tokens=sum(count_tokens(t) for t in texts),
        tool_words=tool_words,
        used_words=used,
        quoted_tokens=quoted,
        json_missing=parsed.answer == final.strip(),
    )


@dataclass(frozen=True)
class ArmUse:
    sessions: int
    mean_tool_tokens: float
    used_share: float
    quoted_share: float
    json_missing_rate: float
    tool_calls_per_correct: float
    used_share_correct: float
    used_share_incorrect: float


def _ratio(num: float, den: float) -> float:
    return num / den if den else float("nan")


def by_arm(
    uses: Iterable[SessionUse], correct: Mapping[tuple[str, str], bool]
) -> dict[str, ArmUse]:
    """Per-arm summary; ``correct`` is the primary judge's verdict per session."""
    groups: dict[str, list[SessionUse]] = {}
    for u in uses:
        groups.setdefault(u.arm, []).append(u)
    out = {}
    for arm, us in sorted(groups.items()):
        ok = [u for u in us if correct.get((u.task_id, arm)) is True]
        bad = [u for u in us if correct.get((u.task_id, arm)) is False]
        out[arm] = ArmUse(
            sessions=len(us),
            mean_tool_tokens=sum(u.tool_tokens for u in us) / len(us),
            used_share=_ratio(
                sum(u.used_words for u in us), sum(u.tool_words for u in us)
            ),
            quoted_share=_ratio(
                sum(u.quoted_tokens for u in us), sum(u.tool_tokens for u in us)
            ),
            json_missing_rate=sum(u.json_missing for u in us) / len(us),
            tool_calls_per_correct=_ratio(sum(u.tool_calls for u in us), len(ok)),
            used_share_correct=_ratio(
                sum(u.used_words for u in ok), sum(u.tool_words for u in ok)
            ),
            used_share_incorrect=_ratio(
                sum(u.used_words for u in bad), sum(u.tool_words for u in bad)
            ),
        )
    return out


def main(argv: list[str] | None = None) -> None:  # pragma: no cover - run output
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=Path("bench/results/m10"))
    args = parser.parse_args(argv)

    with (args.out / "results.csv").open(encoding="utf-8", newline="") as fh:
        correct = {
            (r["task_id"], r["strategy"]): r["correct"] == "1"
            for r in csv.DictReader(fh)
            if r["correct"] != ""
        }
    uses = [
        session_use(s)
        for s in last_sessions(args.out / "transcripts")
        if (s.task_id, s.arm) in correct
    ]
    print(
        "| arm | sessions | tool tokens/session | used share | quoted share | "
        "no JSON | tool calls/correct | used share (correct / incorrect) |"
    )
    print("|---|---|---|---|---|---|---|---|")
    for arm, a in by_arm(uses, correct).items():
        print(
            f"| {arm} | {a.sessions} | {a.mean_tool_tokens:.0f} | {a.used_share:.3f} | "
            f"{a.quoted_share:.3f} | {a.json_missing_rate:.3f} | "
            f"{a.tool_calls_per_correct:.2f} | {a.used_share_correct:.3f} / "
            f"{a.used_share_incorrect:.3f} |"
        )


if __name__ == "__main__":
    main()
