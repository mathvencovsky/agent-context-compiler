"""Per-fixture forbidden-string cleanup check (FEAT-004).

Every real fixture carries site chrome — navigation labels, footer text,
copy-code controls, permalink glyphs, "On this page" tables of contents, cookie
banners, etc. SPEC §4 requires NONE of that leak into any block's text. Here each
fixture gets a hand-picked list of strings that appear in the raw page's chrome
(nav/footer/aside/copy-button/permalink) and the test asserts none of them appear
in any ``block.text`` of the extracted IR.

The strings were chosen by inspecting the saved pages per site family:

- MkDocs-Material (FastAPI, HTTPX, Material docs): "Skip to content",
  "Table of contents", "Back to top", "Copy to clipboard",
  "Made with Material for MkDocs".
- Pydantic (also MkDocs-Material): "Skip to content", "Was this page helpful?".
- Sphinx-style Python stdlib/tutorial + requests + flask: the permalink glyph
  "\u00b6" and "Table of contents" / navigation labels.

Each list is a subset known to live only in removed chrome, so a leak means the
cleanup (nav/footer/aside + copy controls + permalinks) failed.
"""

from __future__ import annotations

import pytest

from app.extract.html import extract_html
from tests.unit.extract._fixtures import fixture_ids, read_fixture, source_for

_PERMALINK = "\u00b6"

# MkDocs-Material shared chrome (navigation drawer, footer, copy buttons, ToC).
_MKDOCS = [
    "Skip to content",
    "Table of contents",
    "Back to top",
    "Copy to clipboard",
    "Made with Material for MkDocs",
]

# Sphinx-style docs (python.org, requests, flask) chrome: permalink glyph + ToC.
_SPHINX = [_PERMALINK, "Table of contents"]

FORBIDDEN: dict[str, list[str]] = {
    # FastAPI (MkDocs-Material)
    "fastapi_body_params": _MKDOCS,
    "fastapi_first_steps": _MKDOCS,
    "fastapi_query_params": _MKDOCS,
    "fastapi_release_notes": _MKDOCS,
    # Material for MkDocs own docs
    "mkdocs_material_admonitions": [*_MKDOCS, "Was this page helpful?"],
    # The changelog QUOTES phrases like "Was this page helpful?" and "Table of
    # contents" inside real release-note entries, so those are not safe forbidden
    # strings here. Keep only chrome labels that never appear as content.
    "mkdocs_material_changelog": [
        "Skip to content",
        "Back to top",
        "Made with Material for MkDocs",
    ],
    "mkdocs_material_code_blocks": [*_MKDOCS, "Was this page helpful?"],
    # HTTPX (MkDocs-Material)
    "httpx_quickstart": [
        "Skip to content",
        "Table of contents",
        "Copy to clipboard",
    ],
    # Pydantic (MkDocs-Material, mkdocstrings)
    "pydantic_changelog": ["Skip to content", "Was this page helpful?"],
    "pydantic_fields": ["Skip to content", "Was this page helpful?"],
    "pydantic_models": ["Skip to content", "Was this page helpful?"],
    # Sphinx-style CPython docs
    "python_stdlib_functions": [_PERMALINK],
    "python_stdlib_argparse": _SPHINX,
    "python_stdlib_pathlib": _SPHINX,
    "python_tutorial_classes": _SPHINX,
    "python_tutorial_datastructures": _SPHINX,
    # Sphinx-style third-party API refs
    "requests_api_reference": _SPHINX,
    "flask_api_reference": [_PERMALINK],
}


@pytest.mark.parametrize("stem", fixture_ids())
def test_no_chrome_string_in_any_block_text(stem: str) -> None:
    forbidden = FORBIDDEN.get(stem)
    assert forbidden, f"no forbidden-string list defined for fixture {stem!r}"

    body = read_fixture(stem)
    doc = extract_html(body, source_for(stem, body))

    for block in doc.blocks:
        for needle in forbidden:
            assert needle not in block.text, (
                f"{stem}: forbidden chrome string {needle!r} leaked into a "
                f"{block.type} block ({block.block_id})"
            )
