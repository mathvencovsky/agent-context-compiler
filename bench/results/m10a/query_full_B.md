# M10a query cleaning, half B (175 tasks)

## Full pipeline (lambda 0.0), hit and tokens

| variant | hit 1000 | tokens 1000 | hit 2000 | tokens 2000 | hit 4000 | tokens 4000 | hit 8000 | tokens 8000 |
|---|---|---|---|---|---|---|---|---|
| current | 0.291 | 955 | 0.440 | 1,950 | 0.594 | 3,921 | 0.691 | 7,357 |
| strip | 0.269 | 951 | 0.423 | 1,949 | 0.571 | 3,918 | 0.669 | 7,335 |

strip vs current, paired (diff [95% CI], McNemar p):

- 1000: -0.023 [-0.057, +0.011], p=0.3438
- 2000: -0.017 [-0.051, +0.017], p=0.5078
- 4000: -0.023 [-0.057, +0.011], p=0.3438
- 8000: -0.023 [-0.057, +0.006], p=0.2891

## Acceptance: strip vs current (DECISIONS 2026-10-16)

- FAIL hit gain at 8000: CI lower bound > 0: -0.023 [-0.057, +0.006], p=0.2891
- FAIL hit at 1000: no drop over 2 pts: -0.023
- PASS hit at 2000: no drop over 2 pts: -0.017
- FAIL hit at 4000: no drop over 2 pts: -0.023
- PASS coverage per 1k tokens at 1000 > strong_rag: 0.325 vs 0.299
- PASS coverage per 1k tokens at 2000 > strong_rag: 0.219 vs 0.212
- PASS coverage per 1k tokens at 4000 > strong_rag: 0.143 vs 0.138
- FAIL coverage per 1k tokens at 8000 > strong_rag: 0.088 vs 0.089
- FAIL median ms at 8000: at most +50%: 6144 vs 2806

**REJECTED**
