# M10a selection variants, half B (176 tasks)

| variant | hit 1000 | tokens 1000 | hit 2000 | tokens 2000 | hit 4000 | tokens 4000 | hit 8000 | tokens 8000 |
|---|---|---|---|---|---|---|---|---|
| current | 0.278 | 938 | 0.420 | 1,874 | 0.551 | 3,476 | 0.608 | 5,341 |
| lambda_0 | 0.290 | 955 | 0.438 | 1,950 | 0.591 | 3,921 | 0.688 | 7,342 |

## Why the selection stopped

- current 1000: {'budget_full': 139, 'adjusted_value_below_min': 37}
- current 2000: {'budget_full': 132, 'adjusted_value_below_min': 44}
- current 4000: {'budget_full': 99, 'adjusted_value_below_min': 77}
- current 8000: {'adjusted_value_below_min': 145, 'budget_full': 31}

## Acceptance: lambda_0 vs current

- PASS hit gain at 8000: CI lower bound > 0: +0.080 [+0.040, +0.125], p=0.0001
- PASS hit at 1000: no drop over 2 pts: +0.011
- PASS hit at 2000: no drop over 2 pts: +0.017
- PASS hit at 4000: no drop over 2 pts: +0.040
- PASS coverage per 1k tokens at 1000 > strong_rag: 0.334 vs 0.297
- PASS coverage per 1k tokens at 2000 > strong_rag: 0.222 vs 0.211
- PASS coverage per 1k tokens at 4000 > strong_rag: 0.146 vs 0.137
- PASS coverage per 1k tokens at 8000 > strong_rag: 0.091 vs 0.088
- PASS median ms at 8000: at most +50%: 2368 vs 2151

**ACCEPTED**
