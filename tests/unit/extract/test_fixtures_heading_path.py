"""heading_path assertions on representative fixtures (FEAT-004).

``heading_path`` is the list of ancestor heading titles from the top level down
to the nearest preceding heading (SPEC §4). These assertions pin the observed
heading_path for known blocks on three representative fixtures — a long CPython
tutorial chapter, the argparse module guide, and a FastAPI tutorial page — so a
regression in the walker's heading-stack logic is caught. The manual sampling
(fixtures, blocks, observed vs expected) is recorded in PROGRESS.md under the M3
section.
"""

from __future__ import annotations

from app.extract.html import extract_html
from app.extract.ir import Block, Document
from tests.unit.extract._fixtures import read_fixture, source_for


def _extract(stem: str) -> Document:
    body = read_fixture(stem)
    return extract_html(body, source_for(stem, body))


def _first_with_text(doc: Document, needle: str) -> Block:
    return next(b for b in doc.blocks if needle in b.text)


def test_heading_path_python_tutorial_classes() -> None:
    doc = _extract("python_tutorial_classes")

    # Top-level chapter heading has an empty path (it IS the top heading).
    top = next(b for b in doc.blocks if b.type == "heading" and b.level == 1)
    assert top.text == "9. Classes"
    assert top.heading_path == []

    # A paragraph directly under the chapter heading.
    intro = _first_with_text(
        doc, "Classes provide a means of bundling data and functionality"
    )
    assert intro.heading_path == ["9. Classes"]

    # A deeply nested block carries the full h1 > h2 > h3 ancestry.
    nested = _first_with_text(doc, "This is an example demonstrating how to reference")
    assert nested.heading_path == [
        "9. Classes",
        "9.2. Python Scopes and Namespaces",
        "9.2.1. Scopes and Namespaces Example",
    ]


def test_heading_path_python_stdlib_argparse() -> None:
    doc = _extract("python_stdlib_argparse")

    # The page has a module-title h1 above the h2 sections; capture its text so
    # the assertions do not pin the (charset-dependent) em-dash in the title.
    top = next(b for b in doc.blocks if b.type == "heading" and b.level == 1)
    assert top.text.startswith("argparse")
    assert top.heading_path == []
    title = top.text

    # The "ArgumentParser objects" h2 sits one level under the module title.
    section = next(
        b
        for b in doc.blocks
        if b.type == "heading" and b.text == "ArgumentParser objects"
    )
    assert section.heading_path == [title]

    # A paragraph under the h2 > h3 "prog" subsection carries the full ancestry.
    prog_para = _first_with_text(
        doc, "By default, ArgumentParser calculates the name of the program"
    )
    assert prog_para.heading_path == [title, "ArgumentParser objects", "prog"]


def test_heading_path_fastapi_first_steps() -> None:
    doc = _extract("fastapi_first_steps")

    # Page title is the top heading with an empty path.
    title = next(b for b in doc.blocks if b.type == "heading" and b.level == 1)
    assert title.text == "First Steps"
    assert title.heading_path == []

    # A paragraph nested under h1 "First Steps" > h3 "OpenAPI" > h4 'API "schema"'.
    deep = _first_with_text(doc, "In this case, OpenAPI is a specification")
    assert deep.heading_path == ["First Steps", "OpenAPI", 'API "schema"']
