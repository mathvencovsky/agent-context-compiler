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

## 2b. Clean the question before searching (rejected on half B, 2026-10-16)

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
distinguishable from zero. Paid calls: 137 rerank, 0 embeddings.

Half B at the human's request (`query_full_B.md`, 175 tasks under the answer
key without inline script/style): **rejected**. Hit 0.291 → 0.269 (1k),
0.440 → 0.423 (2k), 0.594 → 0.571 (4k), 0.691 → 0.669 (8k); 8k paired diff
−2.3 pts, 95% CI [−5.7, +0.6], p = 0.29. Fails the 8k gain, the 1k and 4k
drop limits and coverage per 1k tokens at 8k (0.088 vs strong_rag 0.089).
The latency check also fails (6,144 vs 2,806 ms), but only because the strip
pools were not cached and paid the vendor's latency. The half-A gain did not
replicate: cleaning the question does not help. No change to `app/`. Paid
calls: about 175 rerank and 175 embedding calls (one run cut by a Jina 429,
resumed from the cache).

## 3. Neighbor expansion for split sections (rejected on half B, 2026-10-16)

Half A profile: of the 29 tasks where no chunk holds half the answer section, 25
are sections split across chunks (20 under one heading) and 4 are extraction
losses. Variant: each pick pulls in its `ord` ± 1 neighbors, only at budgets of
4k and up (`expand_A.md`; expanding at every budget lost 3-4 pts at 1k).

| half | variant | hit 4k | hit 8k | tokens 8k |
|---|---|---|---|---|
| A (139) | current | 0.662 | 0.719 | 7,304 |
| A (139) | adj_1_from_4k | 0.669 | 0.763 | 7,647 |
| B (176) | current | 0.591 | 0.688 | 7,342 |
| B (176) | adj_1_from_4k | 0.557 | 0.699 | 7,676 |

Half B (`confirm_adj_1_from_4k_B.md`): 8k +1.1 pts, CI [-3.4, +5.7], p = 0.81;
4k -3.4 pts. Two rules fail; the half A gain did not replicate. No change to
`app/`. 0 paid calls.

## 4. Dominant content container when a page has no main (accepted 2026-10-16)

Pages without `<main>`/`<article>`/`role="main"` (PostgreSQL, SQLite, RFC
Editor) let readability keep a single block. `app/extract/html.py` now descends
from `<body>` while one child holds >= 80% of the non-chrome text and falls back
to that container when readability keeps < 80% of it. PostgreSQL pages keeping
under half their text: 38 -> 5. Item 4 rule: DECISIONS.md 2026-10-16.

| half | variant | extraction hit | hit 1k | hit 2k | hit 4k | hit 8k |
|---|---|---|---|---|---|---|
| A (138) | current | 0.978 | 0.290 | 0.478 | 0.667 | 0.725 |
| A (138) | container_fallback | 1.000 | 0.290 | 0.471 | 0.667 | 0.732 |
| B (175) | current | 0.943 | 0.291 | 0.440 | 0.594 | 0.691 |
| B (175) | container_fallback | 0.989 | 0.280 | 0.451 | 0.617 | 0.720 |

Half B checks (`confirm_container_fallback_B.md`), all pass: no task's
extraction coverage drops; PostgreSQL mean 0.694 -> 0.963 (27 tasks); no budget
drops over 2 pts; coverage per 1k tokens above strong_rag at every budget.
Paid: 42 embedding and 39 rerank calls (one Jina 429, retried).

The answer key now ignores inline script/style/template (2 MDN keys dropped):
313 keys, half A 138, half B 175.

## A1. Adaptive budget: stop at low rerank relevance (rejected on half A, 2026-10-17)

Rule fixed first (DECISIONS.md 2026-10-17). Half A (138 tasks, cache only, 0
paid calls; `adaptive_A.md`): every cut that saves tokens loses coverage beyond
the 0.5 pt tolerance. The mildest, an absolute cut at 0.1, saves 4% of tokens at
4k and 11% at 8k but loses 1.3 pt of coverage at 4k and 2.3 pt at 8k; stronger
cuts lose more. Half B was not opened; `app/` unchanged. The rerank score is not
a reliable stop signal here: low-scored chunks still carry parts of the answer
section.

## A2. Favor whole code blocks for code questions (rejected on half A, 2026-10-17)

Rule fixed first (DECISIONS.md 2026-10-17). Half A (138 tasks, 82 code; cache
only, 0 paid calls; `code_A.md`): no variant raises code-task coverage at 4k or
8k. Boosts of 0.1 and 0.2 keep overall coverage within tolerance but move
code-task coverage by -0.5 to -0.7 pt at 4k and 0 at 8k; 0.3 breaks the overall
tolerance. The query detector flags 127 of 138 queries (precision 0.60 against
`task_type == code`): almost every Stack Overflow question looks like code.
Half B was not opened; `app/` unchanged.

## `<pre>` line-break fix: affected dev tasks re-measured (2026-10-17)

33 keyed dev tasks cite one of the 154 pages whose chunks changed (10 in half A,
23 in half B). NINA before vs after the fix (`pre_remeasure.md`): same coverage
and hits at 1k, 4k and 8k; at 2k one task loses its hit (-3.0 pts, 95% CI
[-9.1, 0.0]). The section-coverage metric barely sees the fix, which matters for
how readable the code is to the agent. Cost: 5 embedding calls and 2 rerank
calls (authorized).
