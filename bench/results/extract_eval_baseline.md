# Extraction eval: baseline and Phase P acceptance rules (M10b)

The "before" for every Phase P extraction change
(`docs/PESQUISA_RODADA3.md` §4 items 3–6, §7 Phase P steps 1–2). Numbers
measured on the frozen extractors (`app/` tree `d878d058`, Amendment 15);
per-page values are in `extract_eval_baseline.json`. Reproduce with
`make extract-eval` (dev + PDFs, compared with this baseline) or
`uv run python -m bench.extract_eval.run --split dev,test`. Paid calls: 0.

## Data

- **WCXB documentation subset**: 131 real documentation pages with
  human-reviewed ground truth (`main_content`, Markdown, `with`/`without`
  snippets). WCXB v1.0 by Murrough Foley, CC-BY-4.0,
  https://github.com/Murrough-Foley/web-content-extraction-benchmark at commit
  `c039d5ee`. Downloaded, never committed; `bench/extract_eval/wcxb_manifest.json`
  pins id, URL and sha256 of every file.
- **Fixed split** (committed in `693e278`, before any measurement with this
  harness): `int(sha256(page_id), 16) % 2`, 0 = dev (64 pages), 1 = test
  (67 pages). It ignores WCXB's own dev/test split, which is recorded too.
- **PDF facts set**: 7 PDFs, 64 facts written by reading the rendered pages,
  not any extractor's output (`bench/extract_eval/pdf_facts.yaml`):
  RISC-V ISA Manual Vol. I 20191213 (pdfTeX, 238 pages, CC-BY-4.0, RISC-V
  Foundation); GeoTopo lecture notes (pdfTeX, German, 117 pages) and five
  small files from `py-pdf/sample-files` (two-column LaTeX with a table, TOC
  and sections, minimal LaTeX, LibreOffice, Google Docs), all CC-BY-SA-4.0.
  Downloaded with checksums, not committed (ShareAlike and size).

## Metrics

Word P/R/F1 (WCXB's official metric, mean over pages); **leak** = share of
extracted tokens whose word never occurs in the ground truth (pure
boilerplate; duplication lowers P but is not leak); **without/with** = WCXB
snippet rates; **glued** = tokens made of two ground-truth words joined at a
case boundary (research front C, non-code text only); **code in text / as
block** = ground-truth fenced code blocks found (whitespace-collapsed)
anywhere in the output / inside the system's code structures; **table pages**
= pages with a ground-truth table where the system emitted a table structure;
**table cells** = ground-truth cells (3+ chars) found in table structures.
CIs: seeded page bootstrap. PDF: fact pass rate, long-token rate (alphabetic
runs of 20+ letters in prose: glued words), text blocks per page, tables and
empty tables. pypdfium2's plain text is a reference row only.

## Baseline

### WCXB-docs, dev (64 pages)

| system | F1 [95% CI] | P | R | leak [95% CI] | without | with | glued tokens (pages) | code in text | code as block | table pages | table cells |
|---|---|---|---|---|---|---|---|---|---|---|---|
| nina | 0.912 [0.872, 0.944] | 0.906 | 0.949 | 4.3% [2.2%, 7.0%] | 4.4% | 70.4% | 251 (11) | 93.5% | 91.9% | 8/9 | 76.9% |
| trafilatura | 0.915 [0.880, 0.945] | 0.955 | 0.904 | 2.3% [1.1%, 3.9%] | 1.9% | 67.8% | 72 (5) | 85.0% | 51.1% | 6/9 | 72.5% |
| readability | 0.844 [0.776, 0.903] | 0.925 | 0.821 | 6.1% [2.5%, 10.4%] | 1.3% | 58.5% | 263 (14) | 80.0% | 80.0% | 8/9 | 79.2% |

NINA code blocks: 987; with language 14.9%; token-class "language" (hljs-
bug) 0; line-number leak 20 blocks; code duplicated inside a text block 180;
readability fallback on 43/64 pages.

### WCXB-docs, test (67 pages)

| system | F1 [95% CI] | P | R | leak [95% CI] | without | with | glued tokens (pages) | code in text | code as block | table pages | table cells |
|---|---|---|---|---|---|---|---|---|---|---|---|
| nina | 0.905 [0.872, 0.934] | 0.877 | 0.959 | 4.3% [2.6%, 6.6%] | 13.8% | 81.2% | 119 (21) | 83.9% | 83.9% | 6/6 | 79.5% |
| trafilatura | 0.927 [0.897, 0.952] | 0.942 | 0.931 | 2.2% [1.1%, 3.7%] | 4.8% | 79.9% | 65 (9) | 81.2% | 67.9% | 5/6 | 61.5% |
| readability | 0.798 [0.733, 0.857] | 0.932 | 0.774 | 1.8% [1.0%, 2.8%] | 3.0% | 54.3% | 173 (22) | 57.1% | 57.1% | 4/6 | 71.3% |

NINA code blocks: 852; with language 21.6%; token-class "language" 0;
line-number leak 1 block; code duplicated inside a text block 85; readability
fallback on 55/67 pages.

### PDF facts

| document | license | facts passed | long-token rate | text blocks/page | tables (empty) | pypdfium2 text facts | NINA text facts |
|---|---|---|---|---|---|---|---|
| riscv_spec_20191213 | CC-BY-4.0 | 1/15 | 32.0% | 25.8 | 164 (0) | 90.0% | 0.0% |
| geotopo | CC-BY-SA-4.0 | 0/12 | 9.7% | 52.5 | 39 (15) | 80.0% | 0.0% |
| latex_multicolumn | CC-BY-SA-4.0 | 1/13 | 23.4% | 34.7 | 0 (0) | 80.0% | 0.0% |
| latex_outline | CC-BY-SA-4.0 | 1/7 | 49.3% | 27.2 | 0 (0) | 100.0% | 0.0% |
| latex_minimal | CC-BY-SA-4.0 | 1/4 | 52.4% | 9.0 | 0 (0) | 100.0% | 0.0% |
| libreoffice_writer | CC-BY-SA-4.0 | 2/4 | 0.0% | 0.0 | 1 (0) | 100.0% | 100.0% |
| google_docs | CC-BY-SA-4.0 | 9/9 | 0.0% | 22.0 | 1 (0) | 100.0% | 100.0% |

All PDFs: 15/64 facts (23.4%); by kind: heading 2/10, order 1/4, paragraphs
1/10, present 4/25, table_cell 3/6, table_count 3/5, title 1/4. Long-token
rate 22.8% (pypdfium2 reference: 0.0% on every file, 0.2% on GeoTopo). Tables
205, empty 15. Every pdfTeX file glues its words; the LibreOffice page comes
out as one table and no paragraph; the two-column page interleaves columns.

### Reading

- HTML: NINA keeps the highest recall (dev 0.949, test 0.959) and keeps code
  as code far better than trafilatura (code as block 91.9% vs 51.1% on dev),
  but leaks about twice as much boilerplate (leak 4.3% vs 2.3%), glues 3.5x
  more words on dev and duplicates code inside list/admonition text. F1 is a
  statistical tie with trafilatura on dev and 2.2 points behind on test; the
  page CIs overlap on both. Code blocks carry a language in only 15-22% of
  cases.
- The `without` rate differs a lot between the halves (4.4% vs 13.8%): a few
  test pages carry most widget leaks. That is why rules below use the paired
  per-page delta, not the raw level.
- PDF: the frozen extractor fails 49 of 64 facts; nearly all failures trace
  to glued words, one block per line, interleaved columns and empty tables.

## Acceptance rules for Phase P extraction changes (fixed 2026-10-28)

Written before any product change; never edited to fit a result. A change
may be declared with one or more targets (boilerplate, boundaries, code,
tables, PDF words, PDF paragraphs, PDF tables). "Δ" is the paired per-page
difference, change minus baseline, on the same pages.

**Gates for every change** (checked automatically by `bench.extract_eval.run`
against this baseline; dev for design, then test once):

- **H1** ΔF1 ≥ −0.002 and the paired 95% CI lower bound ≥ −0.010.
- **H2** Δrecall ≥ −0.005 (recall is NINA's lead; do not trade it away).
- **H5** glued tokens rise by no more than 10% (+2).
- **H7** code-in-text recall and table-cell recall drop by no more than
  0.5 point.
- **P1** no PDF fact that passes in the baseline may fail.
- **P5** no more empty PDF tables than the baseline.

**Targeted thresholds** (on dev; the reviewer checks the printed deltas):

- **H3 boilerplate/chrome selectors:** leak drops by ≥ 25% relative
  (4.3% → ≤ 3.2%) and precision rises by ≥ +0.010, with `without` not rising.
  Selector lists are derived on dev pages only (front C's prototype was tuned
  on all 131 pages and cannot be reused as is).
- **H4 block boundaries / container text:** glued tokens drop by ≥ 50%
  (251 → ≤ 125); if the change also stops copying code into container text,
  code duplicated inside text blocks drops by ≥ 50% (180 → ≤ 90).
- **H6 code language and line numbers:** language rate rises by ≥ 25 points
  (14.9% → ≥ 39.9%), no token-class language, line-number leak blocks 20 → 0,
  and code-as-block recall does not drop.
- **P2 PDF word spacing:** long-token rate ≤ 1.0% on every pdfTeX file and
  ≥ 20 of the 25 `present` facts pass.
- **P3 PDF paragraphs and reading order:** ≥ 8 of 10 `paragraphs` facts and
  ≥ 3 of 4 `order` facts pass.
- **P4 PDF tables:** 0 empty tables and 5 of 5 `table_count` facts pass.
- **End of Phase P step 1 (all PDF changes together):** ≥ 52 of 64 facts
  (80%).

**Confirmation and discipline:**

- **C1** After dev passes, run test exactly once per change
  (`make extract-eval ARGS="--split test"`). Accept only if gates H1, H2, H5
  and H7 pass on test and each targeted metric moves in the expected
  direction by at least half its dev effect. If test fails, the change is
  reverted or redesigned on dev; a second test look needs a dated note in the
  log below, written before running it.
- **C2** The manifest, the split, the facts and these rules are fixed. Any
  change goes into a dated amendment section below, before the measurement it
  affects. `--write-baseline` refuses to overwrite this baseline; the
  post-Phase-P baseline will be a new file.
- **C3** A bug fix in the harness's metric code re-measures this baseline at
  the frozen `app/` tree and records the old and new numbers in the log.
- **C4** PDF facts have no holdout (64 facts). Changes must be generic (no
  rule keyed to a document, font name or string from the facts file); the
  reviewer rejects any change that targets a fact.

**Known limits:** research front C already looked at all 131 pages (dev and
test) with the same extractor, so test is fresh for this harness but not for
the research prototype. WCXB ground truth was drafted by an LLM and reviewed
by humans; `main_content` is plain text, so code and table checks rely on
`main_content_markdown`. The PDF facts are hand-checked but small.

## Test looks and amendments log

- 2026-10-28: baseline measured on dev and test (no product change).
- 2026-10-28: Phase S tree (Amendment 17, `app/` tree `712e30d6`, commit
  `0429c87`) measured on dev and PDFs only, against this baseline: ΔF1
  −0.0004 [−0.0008, −0.0000], Δrecall −0.0007, precision, leak, glued, code,
  tables and all 64 PDF facts unchanged; automatic gates pass. Phase P
  changes start from that tree, so this small loss belongs to Phase S (the
  invisible-text removal), not to Phase P. Test not looked at.
- 2026-10-29: Phase P change H6 (code language + line-number gutters,
  branch `claude/m10b-html`). Targets: code. Language from the `<pre>`, its
  `<code>` children and up to 3 ancestors wrapping no other `<pre>`
  (`language-`, `lang-`, `highlight-source-`, `mw-highlight-lang-`, Sphinx
  `highlight-<lang>`, `brush: <lang>`, `data-lang`/`data-language`); Sphinx
  `default`/`none` name no language; token spans and `hljs-*` never read.
  Gutters stripped by class (`linenos`, `lineno`, `gutter`, `hljs-ln-*`,
  `data-line-number`, ...) or as a classless run of 1, 2, 3... line-start
  numbers glued to code covering >= 80% of lines. Dev: ΔF1 +0.0002
  [+0.0000, +0.0005], Δrecall −0.0001, ΔP +0.0003, leak −0.0002, glued
  251 → 251, code in text 93.5% → 93.5%, code as block 91.9% → 91.9%,
  table cells unchanged, language rate 14.9% → 43.8%, token-class language
  0 → 0, line-number leak 20 → 0, code duplicated in text 180 → 154 (Twilio's
  gutter digits no longer differ from the container copy); gates PASS, H6
  target met. Test (single look): ΔF1 +0.0000, Δrecall −0.0001, glued
  119 → 119, code/table recall unchanged, language rate 21.6% → 54.1%,
  line-number leak 1 → 0, PDF facts 15/64 unchanged; gates PASS. Accepted.
- 2026-10-29: Phase P change "container text" (code/tables/lists copied into
  list-item, admonition, blockquote and definition-list text). Targets:
  boundaries (duplication part of H4). Containers now leave out the nested
  blocks they emit on their own (`pre`, `table`, `ul`/`ol`/`dl` in dd,
  admonitions and quotes; `pre`/`table`/`dl` in list items, now also from
  sub-list items, which were not emitted before); a quote holding only such
  blocks emits no empty quote. Dev, cumulative vs baseline: ΔF1 +0.0103
  [+0.0036, +0.0189], Δrecall −0.0016 (pages whose ground truth repeats the
  text, e.g. kernel DCO, Twilio gutter digits), ΔP +0.0187, leak −0.0010,
  glued 251 → 247, code/table recall unchanged, code blocks 987 → 992,
  code duplicated in text 154 → 4 (baseline 180); gates PASS, H4
  duplication target met. Test (single look, HTML only; PDF untouched by an
  HTML-only change): ΔF1 +0.0083 [+0.0042, +0.0131], Δrecall −0.0015, ΔP
  +0.0172, glued 119 → 94, code duplicated 85 → 1, code/table recall
  unchanged; gates PASS. Accepted.
- 2026-10-29: Phase P change "block boundaries" (H4 glued words), first
  design: a space where any block-level element starts or ends, in container
  text, `_block_text` (paragraph, heading, table text) and table cells. Dev,
  cumulative vs baseline: ΔF1 +0.0176 [+0.0075, +0.0302], Δrecall +0.0081,
  ΔP +0.0232, leak −0.0057, glued 251 → 2, code and table-cell recall
  unchanged; gates PASS. Test (first look): ΔF1 +0.0096, Δrecall +0.0009,
  glued 119 → 34, but table-cell recall 79.5% → 77.0%: **H7 FAIL**. Not
  accepted. The test pages were not opened to diagnose it.
- 2026-10-29 (written before the second test look of this change, rule C1):
  redesign on dev. Table-cell recall depends only on the cell strings
  (`_cell_text`), so the redesign keeps cell strings exactly as in the
  baseline (raw `text_content`) and keeps the separators everywhere else
  (container text, paragraph/heading text, the table block's `text`). No
  rule is derived from test pages; by construction table-cell recall must
  equal the baseline. Dev after redesign: identical to the first design
  (ΔF1 +0.0176, glued 251 → 2, table cells 76.9% → 76.9%). The second test
  look is run once, next, and accepted only under the same C1 criteria.
- 2026-10-29: "block boundaries" second (and last) test look, after the
  note above: ΔF1 +0.0096 [+0.0053, +0.0145], Δrecall +0.0009, ΔP +0.0178,
  leak −0.0008, glued 119 → 34 (94 → 34 for this change, −64%; dev −99%),
  code recall unchanged, table-cell recall 79.5% → 79.5%; gates PASS.
  Accepted. Separators inside table cells stay deferred.
- 2026-10-29: Phase P change H3 (page widgets inside the content, commit
  `18e0286`). Targets: boilerplate. Removed only in `_clean_subtree` (not in
  the content-root measure): `<template>`, `lite-youtube`/`lite-vimeo`;
  class tokens `printfooter`, `mw-pt-languages`, `nocontent`,
  `language-selection`; class-name words (`feedback`, `footer`,
  `breadcrumb(s)`, `pagination`, `pager`, `toc`, `banner`, `cookie(s)`,
  `consent`; plain-`div` ids without `.`/`:` too) on non-heading elements of
  at most 500 chars, no negation word; exact page-action labels ("On this
  page", "Copy page", "Edit this page", ...) and two prefixes. Rules derived
  from dev pages only; the H6 hljs check still reads 0 token-class
  languages. Dev, cumulative vs baseline: ΔF1 +0.0228 [+0.0119, +0.0364],
  Δrecall +0.0076, ΔP +0.0333, leak 4.32% → 3.22% (−0.0110, −25.5%
  relative), without 4.4% → 3.3%, with 70.8% → 70.4%, glued 2, code/table
  recall unchanged; gates PASS, H3 target met (≤ 3.2% rounded; precision
  +0.0101 for this change alone, +0.0333 cumulative). For this change alone
  (vs the previous commit): ΔF1 +0.0052, ΔP +0.0101, Δrecall −0.0005, leak
  3.75% → 3.22% (−14% relative); the relative target is met only against
  the baseline, as its "4.3% → ≤ 3.2%" wording states. Test (single look):
  cumulative ΔF1 +0.0155 [+0.0092, +0.0238], Δrecall −0.0002, ΔP +0.0287,
  leak −0.0080, without −0.0448, glued 34, code/table recall unchanged; for
  this change alone ΔF1 +0.0059, ΔP +0.0109, Δrecall −0.0011, leak −0.0072
  (dev −0.0053); gates PASS, both targeted metrics beyond half their dev
  effect. Accepted.
