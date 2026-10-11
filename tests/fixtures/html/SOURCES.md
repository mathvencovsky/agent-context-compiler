# HTML fixture sources (M3 / FEAT-003)

These `.html` files are **verbatim snapshots** of real public technical-doc
pages, downloaded once through the M1 safe fetcher
(`app.fetch.client.fetch`, which enforces the SSRF guard; `robots.txt` was
checked for every URL via `app.fetch.robots.RobotsChecker` before fetching).
They are stored only as test fixtures for NINA's HTML→IR extraction tests
(FEAT-004) — not redistributed as documentation.

Regenerate with:

```
uv run python tests/fixtures/_download_html_fixtures.py
```

**Fetch date:** 2025-02-14 (UTC). All 18 URLs returned HTTP 200 and were
robots-allowed for our user-agent on that date.

## Coverage categories

| Code | Meaning |
| ---- | ------- |
| a | API reference page with a parameter table |
| b | Long guide |
| c | Page with code tabs / several languages |
| d | Changelog |
| e | Page with admonitions (note/warning/tip callouts) |

All five mandatory categories are represented (a–e).

## Fixtures

| Filename | Source URL | Project / site | License | Category | Notes |
| -------- | ---------- | -------------- | ------- | -------- | ----- |
| `python_stdlib_functions.html` | https://docs.python.org/3/library/functions.html | CPython documentation | PSF License (permissive, BSD-style) | a | Built-in functions reference with parameter/semantics tables. |
| `requests_api_reference.html` | https://requests.readthedocs.io/en/latest/api/ | Requests (python-requests) | Apache-2.0 | a | API reference with parameter lists/tables. |
| `flask_api_reference.html` | https://flask.palletsprojects.com/en/stable/api/ | Flask (Pallets) | BSD-3-Clause | a | API reference with parameter tables. |
| `python_stdlib_pathlib.html` | https://docs.python.org/3/library/pathlib.html | CPython documentation | PSF License (permissive, BSD-style) | a | API reference with method/parameter tables. |
| `python_stdlib_argparse.html` | https://docs.python.org/3/library/argparse.html | CPython documentation | PSF License (permissive, BSD-style) | b | Long how-to style module guide. |
| `python_tutorial_classes.html` | https://docs.python.org/3/tutorial/classes.html | CPython documentation | PSF License (permissive, BSD-style) | b | Long tutorial chapter. |
| `python_tutorial_datastructures.html` | https://docs.python.org/3/tutorial/datastructures.html | CPython documentation | PSF License (permissive, BSD-style) | b | Long tutorial chapter. Substituted for a SQLAlchemy page whose robots.txt was unavailable (fail-closed `robots_unavailable`). |
| `fastapi_first_steps.html` | https://fastapi.tiangolo.com/tutorial/first-steps/ | FastAPI | MIT | c | MkDocs-Material page with highlighted code examples. |
| `fastapi_query_params.html` | https://fastapi.tiangolo.com/tutorial/query-params/ | FastAPI | MIT | c | Code-heavy tutorial page. |
| `pydantic_models.html` | https://docs.pydantic.dev/latest/concepts/models/ | Pydantic | MIT | c | Multiple code blocks / tabbed examples. |
| `httpx_quickstart.html` | https://www.python-httpx.org/quickstart/ | HTTPX (encode) | BSD-3-Clause | c | Quickstart with many code snippets. |
| `mkdocs_material_code_blocks.html` | https://squidfunk.github.io/mkdocs-material/reference/code-blocks/ | Material for MkDocs | MIT | c | Reference page demonstrating code blocks and language tabs. |
| `fastapi_release_notes.html` | https://fastapi.tiangolo.com/release-notes/ | FastAPI | MIT | d | Release notes / changelog. |
| `pydantic_changelog.html` | https://docs.pydantic.dev/latest/changelog/ | Pydantic | MIT | d | Project changelog. |
| `mkdocs_material_changelog.html` | https://squidfunk.github.io/mkdocs-material/changelog/ | Material for MkDocs | MIT | d | Project changelog. Substituted for `python-httpx.org/changelog/`, which returned HTTP 404 (no changelog page on that docs site). |
| `mkdocs_material_admonitions.html` | https://squidfunk.github.io/mkdocs-material/reference/admonitions/ | Material for MkDocs | MIT | e | Reference page full of note/warning/tip admonition callouts. |
| `fastapi_body_params.html` | https://fastapi.tiangolo.com/tutorial/body/ | FastAPI | MIT | e | Tutorial with info/tip/note admonitions. |
| `pydantic_fields.html` | https://docs.pydantic.dev/latest/concepts/fields/ | Pydantic | MIT | e | Concept page with note/warning admonitions. |

## License note

Every source above is published under a permissive license
(MIT / BSD-3-Clause / Apache-2.0 / PSF — all OSI-approved and compatible with
storing a local snapshot for automated testing). No GPL/AGPL content is
included, consistent with the project's licensing policy (CLAUDE.md).

## Substitutions (robots-disallowed / unreachable URLs)

Two originally-planned URLs were replaced during the download run; both
replacements come from projects already represented above and keep each
mandatory category covered:

- `https://docs.sqlalchemy.org/en/20/orm/quickstart.html` (category b) —
  skipped because its `robots.txt` could not be retrieved, so the fetcher
  failed closed with `robots_unavailable`. Replaced by
  `python_tutorial_datastructures.html`.
- `https://www.python-httpx.org/changelog/` (category d) — returned HTTP 404
  (the HTTPX docs site has no changelog page). Replaced by
  `mkdocs_material_changelog.html`.
