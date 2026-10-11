"""One-off downloader for real HTML extraction fixtures (M3 / FEAT-003).

This is NOT a pytest test: the leading underscore keeps pytest from collecting
it, and it is meant to be run manually exactly once with

    uv run python tests/fixtures/_download_html_fixtures.py

to populate ``tests/fixtures/html/`` with real public technical-doc pages that
the FEAT-004 acceptance tests consume. It saves the raw response bytes verbatim
so the extraction tests exercise real-world markup.

NON-NEGOTIABLE (CLAUDE.md): all outbound network goes through the M1 safe
fetcher ``app.fetch.client.fetch`` — never httpx/requests/urllib directly. This
script also consults ``app.fetch.robots.RobotsChecker`` before every download so
robots.txt is honoured; a disallowed URL is skipped and reported, never forced.

The ``FIXTURES`` list pairs a descriptive filename stem with a source URL and
the mandatory coverage category it contributes:

    a = API reference page with a parameter table
    b = long guide
    c = page with code tabs / several languages
    d = changelog
    e = page with admonitions (note/warning/tip callouts)

See ``tests/fixtures/html/SOURCES.md`` for the full per-fixture provenance.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Running this file directly puts its own directory on sys.path[0], not the
# repository root, so ``import app`` fails. Add the repo root (three levels up:
# tests/fixtures/_download_html_fixtures.py -> repo) before importing app.*.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.fetch.client import fetch  # noqa: E402
from app.fetch.errors import FetchError, RobotsDisallowed  # noqa: E402
from app.fetch.robots import RobotsChecker  # noqa: E402

# (filename_stem, url, category) — category is informational only (see module
# docstring and SOURCES.md). At least 15 entries, all five categories present.
FIXTURES: list[tuple[str, str, str]] = [
    # (a) API reference pages with parameter tables.
    (
        "python_stdlib_functions",
        "https://docs.python.org/3/library/functions.html",
        "a",
    ),
    (
        "requests_api_reference",
        "https://requests.readthedocs.io/en/latest/api/",
        "a",
    ),
    (
        "flask_api_reference",
        "https://flask.palletsprojects.com/en/stable/api/",
        "a",
    ),
    # (b) long guides.
    (
        "python_stdlib_argparse",
        "https://docs.python.org/3/library/argparse.html",
        "b",
    ),
    (
        "python_tutorial_classes",
        "https://docs.python.org/3/tutorial/classes.html",
        "b",
    ),
    (
        "python_tutorial_datastructures",
        "https://docs.python.org/3/tutorial/datastructures.html",
        "b",
    ),
    # (c) code-tabs / multi-language pages.
    (
        "fastapi_first_steps",
        "https://fastapi.tiangolo.com/tutorial/first-steps/",
        "c",
    ),
    (
        "fastapi_query_params",
        "https://fastapi.tiangolo.com/tutorial/query-params/",
        "c",
    ),
    (
        "pydantic_models",
        "https://docs.pydantic.dev/latest/concepts/models/",
        "c",
    ),
    # (d) changelogs.
    (
        "fastapi_release_notes",
        "https://fastapi.tiangolo.com/release-notes/",
        "d",
    ),
    (
        "pydantic_changelog",
        "https://docs.pydantic.dev/latest/changelog/",
        "d",
    ),
    (
        "mkdocs_material_changelog",
        "https://squidfunk.github.io/mkdocs-material/changelog/",
        "d",
    ),
    # (e) admonition-heavy pages (note/warning/tip callouts).
    (
        "mkdocs_material_admonitions",
        "https://squidfunk.github.io/mkdocs-material/reference/admonitions/",
        "e",
    ),
    (
        "fastapi_body_params",
        "https://fastapi.tiangolo.com/tutorial/body/",
        "e",
    ),
    (
        "pydantic_fields",
        "https://docs.pydantic.dev/latest/concepts/fields/",
        "e",
    ),
    # Extra pages for margin / variety.
    (
        "python_stdlib_pathlib",
        "https://docs.python.org/3/library/pathlib.html",
        "a",
    ),
    (
        "httpx_quickstart",
        "https://www.python-httpx.org/quickstart/",
        "c",
    ),
    (
        "mkdocs_material_code_blocks",
        "https://squidfunk.github.io/mkdocs-material/reference/code-blocks/",
        "c",
    ),
]

_OUTPUT_DIR = Path(__file__).resolve().parent / "html"


def main() -> int:
    """Download every fixture URL through the safe fetcher. Returns exit code."""
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    robots = RobotsChecker()

    saved = 0
    disallowed = 0
    failed = 0

    for stem, url, _category in FIXTURES:
        try:
            robots.is_allowed(url)
            result = fetch(url)
        except RobotsDisallowed:
            disallowed += 1
            print(f"robots-disallowed  {stem:<34} {url}")
            continue
        except FetchError as exc:
            failed += 1
            print(f"failed[{exc.code}]  {stem:<34} {url}")
            continue

        if result.status != 200:
            failed += 1
            print(f"failed[http {result.status}]  {stem:<34} {url}")
            continue

        destination = _OUTPUT_DIR / f"{stem}.html"
        destination.write_bytes(result.body)
        saved += 1
        print(f"ok  {stem:<34} {len(result.body):>8} bytes  {url}")

    print(
        f"\nsaved={saved}  robots-disallowed={disallowed}  failed={failed}  "
        f"total={len(FIXTURES)}"
    )
    # Non-zero exit if we could not meet the 15-fixture floor so the failure is
    # visible to the caller / CI rather than silently passing.
    return 0 if saved >= 15 else 1


if __name__ == "__main__":
    sys.exit(main())
