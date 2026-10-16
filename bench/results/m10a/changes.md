# M10a Phase 3 — accepted changes

Each change is tuned on dev half A and accepted only if it passes the rule in
DECISIONS.md (2026-10-16) on dev half B. Hit = section coverage ≥ 0.5.
Pilot and holdout tasks were not used.

## 1. `selection_lambda` 0.3 → 0.0 (accepted 2026-10-16)

Cause: the diversity penalty pushed the adjusted value below `min_value = 0`
and ended the selection with budget left (8k, current: 116 of 139 tasks on half
A, 145 of 176 on half B).

| half | variant | hit 1k | hit 2k | hit 4k | hit 8k | tokens 8k |
|---|---|---|---|---|---|---|
| A (139, tune) | current | 0.302 | 0.439 | 0.583 | 0.647 | 5,397 |
| A (139, tune) | λ = 0 | 0.288 | 0.475 | 0.662 | 0.719 | 7,304 |
| B (176, confirm) | current | 0.278 | 0.420 | 0.551 | 0.608 | 5,341 |
| B (176, confirm) | λ = 0 | 0.290 | 0.438 | 0.591 | 0.688 | 7,342 |

Half B checks (`confirm_lambda_0_B.md`), all pass:

- 8k gain +8.0 pts, paired bootstrap 95% CI [+4.0, +12.5], McNemar p = 0.0001;
- other budgets: +1.1 (1k), +1.7 (2k), +4.0 (4k);
- coverage per 1k tokens above strong_rag at every budget: 0.334 vs 0.297
  (1k), 0.222 vs 0.211 (2k), 0.146 vs 0.137 (4k), 0.091 vs 0.088 (8k);
- median time at 8k: 2,368 vs 2,151 ms (+10%; selection only, providers cached).

Paid calls: 0 (all providers served from the disk cache).
