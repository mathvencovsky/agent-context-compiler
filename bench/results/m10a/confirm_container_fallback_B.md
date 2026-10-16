# M10a extraction fallback, half B (175 tasks)

| variant | extraction hit | hit 1000 | tokens 1000 | hit 2000 | tokens 2000 | hit 4000 | tokens 4000 | hit 8000 | tokens 8000 |
|---|---|---|---|---|---|---|---|---|---|
| current | 0.943 | 0.291 | 955 | 0.440 | 1,950 | 0.594 | 3,921 | 0.691 | 7,357 |
| container_fallback | 0.989 | 0.280 | 954 | 0.451 | 1,949 | 0.617 | 3,915 | 0.720 | 7,364 |

## Acceptance: container_fallback vs current

- PASS extraction coverage: no task drops: 0 drop
- PASS extraction coverage: PostgreSQL mean rises (27 tasks): 0.694 -> 0.963
- PASS hit at 1000: no drop over 2 pts: -0.011
- PASS hit at 2000: no drop over 2 pts: +0.011
- PASS hit at 4000: no drop over 2 pts: +0.023
- PASS hit at 8000: no drop over 2 pts: +0.029
- PASS coverage per 1k tokens at 1000 > strong_rag: 0.337 vs 0.299
- PASS coverage per 1k tokens at 2000 > strong_rag: 0.228 vs 0.212
- PASS coverage per 1k tokens at 4000 > strong_rag: 0.153 vs 0.138
- PASS coverage per 1k tokens at 8000 > strong_rag: 0.094 vs 0.089
- PASS median ms at 8000: at most +50%: 2827 vs 3364

**ACCEPTED**
