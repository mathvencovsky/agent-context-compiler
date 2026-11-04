"""Where the money went in bench/results/m10b/real_use_2026-11-03 (reads only).

For each round and arm: cost of the main model's cache writes, cache reads and
output, the inner small model's cost, turns and tool calls. Then the spread of the
paired log-cost differences, which sizes the next measurement.

    uv run python scripts/real_use_breakdown.py
"""

from __future__ import annotations

import json
import math
import statistics
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

BASE = Path("bench/results/m10b/real_use_2026-11-03")
ROUNDS = {"1 original": "runs", "2 compact": "runs_v2", "3 leitura fixa": "runs_v3"}
# bench/prices.yaml, Sonnet 5.5; Claude Code writes the 1-hour cache.
PRICES_PER_MTOK = {"input": 2.0, "cache_write": 4.0, "cache_read": 0.10, "output": 10.0}


def parts(summary: Mapping[str, Any]) -> dict[str, float]:
    """Dollar cost of each usage kind on the main model, plus the inner model."""
    out = {
        kind: (summary.get(kind) or 0) * price / 1_000_000
        for kind, price in PRICES_PER_MTOK.items()
    }
    inner = {m: c for m, c in summary["models"].items() if "haiku" in m}
    out["inner"] = sum(inner.values())
    return out


def log_spread(pairs: Sequence[tuple[float, float]]) -> tuple[float, float]:
    """Mean and standard deviation of log(with / without)."""
    logs = [math.log(a / b) for a, b in pairs]
    return statistics.mean(logs), statistics.stdev(logs)


def pairs_needed(spread: float, ratio: float = 0.8) -> int:
    """Pairs to detect ``ratio`` with 80% power at two-sided 5% (normal approx)."""
    return math.ceil(((1.96 + 0.8416) * spread / abs(math.log(ratio))) ** 2)


def main() -> None:
    all_logs: list[float] = []
    for name, folder in ROUNDS.items():
        print(f"\n## Rodada {name}")
        pairs = []
        for arm in ("nina", "sem"):
            rows = [
                json.loads(f.read_text(encoding="utf-8"))
                for f in sorted((BASE / folder).glob(f"Q*_{arm}.summary.json"))
            ]
            total: Counter[str] = Counter()
            tools: Counter[str] = Counter()
            for row in rows:
                total.update(parts(row))
                tools.update(row["tools"])
            cost = sum(r["cost_usd"] for r in rows)
            turns = sum(r["turns"] for r in rows)
            print(
                f"{arm:5} total {cost:.4f} | escrita {total['cache_write']:.4f} "
                f"leitura {total['cache_read']:.4f} saida {total['output']:.4f} "
                f"interno {total['inner']:.4f} | voltas {turns} | {dict(tools)}"
            )
            pairs.append([r["cost_usd"] for r in rows])
        round_pairs = list(zip(pairs[0], pairs[1], strict=True))
        mean, spread = log_spread(round_pairs)
        all_logs += [math.log(a / b) for a, b in round_pairs]
        print(f"log(com/sem): media {mean:+.3f}, desvio {spread:.3f}")
    spread = statistics.stdev(all_logs)
    print(
        f"\nDesvio nas 3 rodadas: {spread:.3f}; "
        f"pares para ver 20%: {pairs_needed(spread)}"
    )


if __name__ == "__main__":
    assert pairs_needed(0.5) == 40
    assert log_spread([(2.0, 1.0), (2.0, 1.0)]) == (math.log(2), 0.0)
    main()
