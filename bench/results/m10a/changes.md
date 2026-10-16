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

## 2. Lexical ranking for the rerank pool (rejected on half A, 2026-10-16)

Half A, answer chunk in the fused top-30 (ranking only, `pool_A.md`): current
87/139, BM25 87, lexical weight 0.25 90 (best), title-only query 84, vector only
81. Of the 23 tasks whose answer chunk misses the pool, 10 are outside both
top-50 lists, and no variant recovers more than 3: the bottleneck is the long
question (title + body with code) as the query, not the lexical scorer. No
change to `app/`; not taken to half B. 0 paid calls.

## 3. Clean the question before searching (rejected on half A, 2026-10-16)

`bench/m10a_query.py` drops code-like lines (indented, or at least 12% code
symbols) and Stack Overflow filler from the body, keeping the title. Median
body keeps 80% of its words; 15 of 1,809 dropped lines look like prose. Half A,
answer chunk in the fused top-30 (ranking only, `query_A.md`): current 87/139,
cleaned on the lexical side 89, cleaned on both sides 89, title plus first 60
cleaned words 84. The cleaned query embedding adds nothing over cleaning the
lexical side alone. +2 tasks is within the spread of the lexical variants in
item 2 (none taken to half B). Paid calls: 139 batched OpenAI embedding calls
(new query texts), 0 rerank.

Full pipeline on half A anyway, at the human's request (`query_full_A.md`;
cleaned query for search, full question for the reranker, λ = 0): hit
0.288 → 0.288 (1k), 0.475 → 0.504 (2k), 0.662 → 0.655 (4k), 0.719 → 0.748
(8k), same tokens. 8k paired diff +2.9 pts, 95% CI [−0.7, +7.2], McNemar
p = 0.29. Smaller than neighbor expansion on half A (+4.4 at 8k) and not
distinguishable from zero; not taken to half B, no change to `app/`. Paid
calls: 137 rerank, 0 embeddings.
