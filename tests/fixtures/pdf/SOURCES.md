# PDF extraction fixtures (M4 / FEAT-003)

Every PDF in this directory is **GENERATED** — not downloaded — by
`tests/fixtures/_generate_pdf_fixtures.py` using
[reportlab](https://pypi.org/project/reportlab/) (BSD licence, a dev-only
dependency). Generation is deterministic and needs no network, so the files are
checked in and consumed by `tests/unit/extract/test_pdf.py`.

Regenerate with:

```
uv run python tests/fixtures/_generate_pdf_fixtures.py
```

| File | What it exercises |
| --- | --- |
| `api_reference.pdf` | Title in a 24pt font (inferred `heading` level 1); section headings in 16pt (level 2); a **header-ful** table (`Code / Status / Meaning`); a running header AND footer repeated on every page (removed by the >50%-of-pages rule); multiple pages; enough body text that `low_text_content` does NOT fire. |
| `config_guide.pdf` | Same structure as above with a two-column header-ful table (`Setting / Default`); title/heading font levels and repeated header/footer removal. |
| `release_notes.pdf` | Title + section headings; a **header-less** table (its first row has a blank leading cell, so no complete header row is detectable) that triggers the `table_no_header` warning; short body text so `low_text_content` fires; repeated header/footer removal. |
| `textless.pdf` | NO extractable text at all — only a drawn vector rectangle. `extract_pdf` raises `ExtractionEmpty` (HTTP 422); OCR is out of scope. |

The repeated header text is `NINA Technical Reference — Confidential Draft` and
the repeated footer is `© 2026 NINA Project — Page chrome, not content <n>`
(the page number varies, which is why the extractor strips a leading/trailing
page-number token before comparing band lines — see DECISIONS.md 2026-10-11).
