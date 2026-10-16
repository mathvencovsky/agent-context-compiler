# M10a neighbor expansion, half B (176 tasks)

| variant | hit 1000 | tokens 1000 | hit 2000 | tokens 2000 | hit 4000 | tokens 4000 | hit 8000 | tokens 8000 |
|---|---|---|---|---|---|---|---|---|
| current | 0.290 | 955 | 0.438 | 1,950 | 0.591 | 3,921 | 0.688 | 7,342 |
| adj_1_from_4k | 0.290 | 955 | 0.438 | 1,950 | 0.557 | 3,946 | 0.699 | 7,676 |

## Acceptance: adj_1_from_4k vs current

- FAIL hit gain at 8000: CI lower bound > 0: +0.011 [-0.034, +0.057], p=0.8145
- PASS hit at 1000: no drop over 2 pts: +0.000
- PASS hit at 2000: no drop over 2 pts: +0.000
- FAIL hit at 4000: no drop over 2 pts: -0.034
- PASS coverage per 1k tokens at 1000 > strong_rag: 0.334 vs 0.297
- PASS coverage per 1k tokens at 2000 > strong_rag: 0.222 vs 0.211
- PASS coverage per 1k tokens at 4000 > strong_rag: 0.139 vs 0.137
- PASS coverage per 1k tokens at 8000 > strong_rag: 0.089 vs 0.088
- PASS median ms at 8000: at most +50%: 2327 vs 2117

**REJECTED**
