# M10a response formats (313 keyed dev tasks)

Mean tokens the agent receives per `assemble` call; in parentheses, the change against `detailed_mcp`. Concise holds every fragment verbatim (asserted).

## all

| budget | detailed_mcp | detailed_compact | concise | md_only |
|---|---|---|---|---|
| 1000 | 2,801 (+0%) | 2,519 (-10%) | 1,086 (-61%) | 861 (-69%) |
| 2000 | 4,658 (+0%) | 4,253 (-9%) | 2,122 (-54%) | 1,812 (-61%) |
| 4000 | 7,952 (+0%) | 7,358 (-7%) | 4,124 (-48%) | 3,687 (-54%) |
| 8000 | 13,184 (+0%) | 12,336 (-6%) | 7,592 (-42%) | 6,978 (-47%) |

## A

| budget | detailed_mcp | detailed_compact | concise | md_only |
|---|---|---|---|---|
| 1000 | 2,799 (+0%) | 2,516 (-10%) | 1,075 (-62%) | 852 (-70%) |
| 2000 | 4,661 (+0%) | 4,255 (-9%) | 2,118 (-55%) | 1,810 (-61%) |
| 4000 | 7,934 (+0%) | 7,341 (-7%) | 4,112 (-48%) | 3,680 (-54%) |
| 8000 | 13,114 (+0%) | 12,272 (-6%) | 7,567 (-42%) | 6,961 (-47%) |

## B

| budget | detailed_mcp | detailed_compact | concise | md_only |
|---|---|---|---|---|
| 1000 | 2,802 (+0%) | 2,521 (-10%) | 1,096 (-61%) | 868 (-69%) |
| 2000 | 4,656 (+0%) | 4,252 (-9%) | 2,126 (-54%) | 1,814 (-61%) |
| 4000 | 7,966 (+0%) | 7,372 (-7%) | 4,134 (-48%) | 3,692 (-54%) |
| 8000 | 13,239 (+0%) | 12,386 (-6%) | 7,611 (-43%) | 6,991 (-47%) |

Section coverage, concise minus md_only: 73 of 1252 task-budgets differ (0 lower, 73 higher). Higher means a fragment header (its heading path) matched a heading unit of the section; no fragment text is lost.
