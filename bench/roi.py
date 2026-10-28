"""ROI calculator for M10 (PREREG Amendment 4 point 2; Phase 5).

A model built from measured numbers, not a measurement. After the lookup, a
real session keeps working, and the tool result stays in the context, re-read
from cache on every later turn. For K more turns, a task's cost is

    C(K) = C_session + K × (T × p_read + O)

with ``C_session`` the session's measured USD, ``T`` its measured tool-result
tokens, ``p_read`` the cache-read price per token and ``O`` the cost per turn
of everything else. Over tasks, CPT(K) = Σ C(K) / Σ correct, and

    R(K) = CPT_full_page(K) / CPT_nina(K)

with the PREREG §6 paired bootstrap over tasks (10,000 resamples, seed
``20261015``, percentile interval).

**Context editing** (docs/CONTEXT_ENGINEERING.md, P5): a client that clears
old tool results (Anthropic's ``clear_tool_uses``) stops paying for them after
J turns, so only ``min(K, J)`` turns re-read ``T``. ``--clear-after J`` adds
this scenario, which shows the advantage a customer with clearing really gets
instead of the largest one. Stated assumptions, as in Amendment 4: every later
turn hits the cache, success does not change with K, and the one cache rewrite
that clearing causes is left out (it is the same order of size for both arms).

Usage (after the main run)::

    uv run python -m bench.roi --p-read 0.10 [--o 0.0] [--clear-after 3] \
        [--volume 1000] [--price-scale 1.0]
"""

from __future__ import annotations

import argparse
import csv
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

SEED = 20261015
N_BOOT = 10_000
KS = (0, 10, 30, 100)
RESULTS = Path("bench/results/m10/results.csv")


@dataclass(frozen=True)
class TaskCost:
    """One arm's measured session for one task."""

    cost_usd: float
    tool_tokens: int
    correct: bool


def cost_at(
    t: TaskCost, k: int, *, o: float, p_read: float, clear_after: int | None
) -> float:
    """C(K) for one task; ``p_read`` in USD per token."""
    rereads = k if clear_after is None else min(k, clear_after)
    return t.cost_usd + rereads * t.tool_tokens * p_read + k * o


def cpt(
    tasks: Sequence[TaskCost],
    k: int,
    *,
    o: float,
    p_read: float,
    clear_after: int | None = None,
) -> float:
    """Cost per correct task at K; infinite when nothing is correct."""
    correct = sum(t.correct for t in tasks)
    total = sum(cost_at(t, k, o=o, p_read=p_read, clear_after=clear_after)
                for t in tasks)  # fmt: skip
    return total / correct if correct else float("inf")


def ratio(
    full: Sequence[TaskCost],
    nina: Sequence[TaskCost],
    k: int,
    *,
    o: float,
    p_read: float,
    clear_after: int | None = None,
) -> float:
    """R(K) = CPT(full_page) / CPT(nina) on paired tasks."""
    kw = {"o": o, "p_read": p_read, "clear_after": clear_after}
    return cpt(full, k, **kw) / cpt(nina, k, **kw)  # type: ignore[arg-type]


def bootstrap_ratio(
    full: Sequence[TaskCost],
    nina: Sequence[TaskCost],
    k: int,
    *,
    o: float,
    p_read: float,
    clear_after: int | None = None,
    n_boot: int = N_BOOT,
    seed: int = SEED,
) -> tuple[float, float, float]:
    """R(K) and its 95% paired percentile interval (resampling tasks)."""
    n = len(full)
    rng = random.Random(seed)
    kw = {"o": o, "p_read": p_read, "clear_after": clear_after}
    boots = []
    for _ in range(n_boot):
        idx = [rng.randrange(n) for _ in range(n)]
        boots.append(
            ratio([full[i] for i in idx], [nina[i] for i in idx], k, **kw)  # type: ignore[arg-type]
        )
    boots.sort()
    point = ratio(full, nina, k, **kw)  # type: ignore[arg-type]
    return point, boots[int(0.025 * n_boot)], boots[int(0.975 * n_boot) - 1]


def paired_tasks(
    rows: Sequence[Mapping[str, str]], a: str = "full_page", b: str = "nina"
) -> tuple[list[TaskCost], list[TaskCost]]:
    """Primary-population tasks where both arms have a judged session."""
    by: dict[tuple[str, str], TaskCost] = {}
    for r in rows:
        if r.get("split") != "holdout" or r.get("correct", "") == "":
            continue
        by[(r["task_id"], r["strategy"])] = TaskCost(
            cost_usd=float(r["cost_usd"]),
            tool_tokens=int(r["tool_result_tokens"]),
            correct=r["correct"] == "1",
        )
    ids = sorted({t for t, s in by if s == a} & {t for t, s in by if s == b})
    return [by[(t, a)] for t in ids], [by[(t, b)] for t in ids]


def report(
    full: Sequence[TaskCost],
    nina: Sequence[TaskCost],
    *,
    p_read: float,
    o: float,
    clear_after: int | None,
    volume: int,
    price_scale: float = 1.0,
    ks: Sequence[int] = KS,
    n_boot: int = N_BOOT,
) -> str:
    """Markdown: R(K) with intervals, without and with clearing, and savings."""
    scale = price_scale

    def scaled(tasks: Sequence[TaskCost]) -> list[TaskCost]:
        return [TaskCost(t.cost_usd * scale, t.tool_tokens, t.correct) for t in tasks]

    full_s, nina_s = scaled(full), scaled(nina)
    p, oo = p_read * scale, o * scale
    scenarios: list[tuple[str, int | None]] = [("no clearing", None)]
    if clear_after is not None:
        scenarios.append(
            (f"tool results cleared after {clear_after} turns", clear_after)
        )
    lines = [
        f"# ROI model ({len(full)} paired tasks; a model, not a measurement)",
        "",
        f"p_read = {p_read * 1e6:.4f} USD/1M tokens, O = {o:.6f} USD/turn, "
        f"price scale {price_scale}.",
        "",
        "| scenario | K | R(K) | 95% interval | CPT full_page | CPT nina "
        f"| saving per {volume:,} correct tasks |",
        "|---|---|---|---|---|---|---|",
    ]
    for name, clear in scenarios:
        kw = {"o": oo, "p_read": p, "clear_after": clear}
        for k in ks:
            r, lo, hi = bootstrap_ratio(full_s, nina_s, k, n_boot=n_boot, **kw)  # type: ignore[arg-type]
            a, b = cpt(full_s, k, **kw), cpt(nina_s, k, **kw)  # type: ignore[arg-type]
            lines.append(
                f"| {name} | {k} | {r:.2f} | [{lo:.2f}, {hi:.2f}] | {a:.4f} "
                f"| {b:.4f} | {volume * (a - b):,.2f} |"
            )
    lines += [
        "",
        "Assumptions: every later turn hits the cache; success does not change "
        "with K; the cache rewrite after clearing is left out.",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> None:  # pragma: no cover - needs results
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--results", type=Path, default=RESULTS)
    parser.add_argument("--p-read", type=float, required=True,
                        help="cache-read price, USD per 1M tokens")  # fmt: skip
    parser.add_argument("--o", type=float, default=0.0, help="USD per other turn")
    parser.add_argument("--clear-after", type=int, help="turns before clearing")
    parser.add_argument("--volume", type=int, default=1000)
    parser.add_argument("--price-scale", type=float, default=1.0)
    args = parser.parse_args(argv)
    with args.results.open(encoding="utf-8") as fh:
        full, nina = paired_tasks(list(csv.DictReader(fh)))
    print(
        report(
            full,
            nina,
            p_read=args.p_read / 1e6,
            o=args.o,
            clear_after=args.clear_after,
            volume=args.volume,
            price_scale=args.price_scale,
        )
    )


if __name__ == "__main__":
    main()
