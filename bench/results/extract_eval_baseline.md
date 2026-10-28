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
