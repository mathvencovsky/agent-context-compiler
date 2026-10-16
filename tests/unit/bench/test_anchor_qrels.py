"""Offline tests for the M10a anchor answer key (bench/anchor_qrels.py)."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from app.fetch.client import FetchResult
from app.fetch.render import RenderResult
from bench.anchor_qrels import anchor_links, section_text, task_key
from bench.corpus_store import CorpusStore
from bench.snapshot import SiteConfig, snapshot
from bench.tasks_schema import ExternalTask, Source

_SPHINX = (
    "<html><body><dl class='py function'>"
    "<dt class='sig' id='print'><span>print</span>(<em>*objects</em>, <em>sep=' '</em>)"
    "<a class='headerlink' href='#print'>¶</a></dt>"
    "<dd><p>Print objects to the text stream file, separated by sep.</p></dd>"
    "</dl><p>Unrelated text after.</p></body></html>"
)
_MDN = (
    "<html><body><section aria-labelledby='syntax'>"
    "<h2 id='syntax'><a href='#syntax'>Syntax</a></h2>"
    "<div class='code-example'><pre>fetch(resource, options)</pre></div></section>"
    "<section aria-labelledby='parameters'><h2 id='parameters'>Parameters</h2>"
    "<p>Another section entirely.</p></section></body></html>"
)
_POSTGRES = (
    "<html><body><div class='sect2' id='SQL-SELECT-LIMIT'>"
    "<div class='titlepage'><h3>LIMIT Clause</h3></div>"
    "<p>The LIMIT clause consists of two independent sub-clauses.</p></div>"
    "<p>Next part.</p></body></html>"
)
_BARE = (
    "<html><body><h2 id='a'>A</h2><!-- a comment --><p>one two three four.</p>"
    "<h3>Sub</h3><p>five six seven eight.</p>"
    "<h2>B</h2><p>nine ten eleven twelve.</p></body></html>"
)


def test_api_entry_takes_the_whole_definition() -> None:
    text = section_text(_SPHINX, "print")
    assert text is not None
    assert "print(*objects, sep=' ')" in text
    assert "Print objects to the text stream file" in text
    assert "Unrelated" not in text


def test_heading_inside_a_wrapper_takes_the_wrapper_only() -> None:
    text = section_text(_MDN, "syntax")
    assert text is not None
    assert "fetch(resource, options)" in text and "Another section" not in text


def test_wrapper_with_the_id_is_the_section() -> None:
    text = section_text(_POSTGRES, "SQL-SELECT-LIMIT")
    assert text == (
        "LIMIT Clause\nThe LIMIT clause consists of two independent sub-clauses."
    )


def test_bare_heading_runs_to_the_next_heading_of_its_level() -> None:
    assert section_text(_BARE, "a") == (
        "A\none two three four.\nSub\nfive six seven eight."
    )


def test_empty_anchor_takes_the_next_element_and_cells_get_spaces() -> None:
    html = (
        "<html><body><span id='x'></span><!-- c --><table><tr><td>name</td>"
        "<td>value</td>"
        "</tr></table></body></html>"
    )
    assert section_text(html, "x") == "name value"
    assert section_text(html, "missing") is None
    assert section_text("<html><body><a name='n'>here it is</a></body></html>", "n")


def test_anchor_links_keeps_only_allowlisted_links_with_fragments() -> None:
    html = (
        '<a href="http://docs.python.org/3/library/functions.html#print">a</a>'
        '<a href="https://docs.python.org/3/library/functions.html">no anchor</a>'
        '<a href="https://example.com/x#y">not docs</a>'
        '<a href="https://DOCS.python.org/3/library/functions.html#print">dup</a>'
    )
    assert anchor_links(html) == [
        ("https://docs.python.org/3/library/functions.html", "print")
    ]


def test_task_key_takes_the_first_usable_anchor_and_names_the_reasons(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = (
        "<html><body><h2 id='tiny'>Tiny</h2>"
        "<h2 id='real'>Real</h2><p>The answer lives in this sentence here.</p>"
        "</body></html>"
    )

    def fake_fetch(url: str) -> FetchResult:
        return FetchResult(
            final_url=url,
            status=200,
            headers=httpx.Headers({"content-type": "text/html"}),
            body=page.encode(),
            content_type="text/html",
            redirects=0,
            elapsed_ms=1,
        )

    def fake_render(url: str) -> RenderResult:
        return RenderResult(final_url=url, html="", status=200)

    url = "https://docs.python.org/3/x.html"
    site = SiteConfig(
        site="P", key="python", rich_categories=[], render="never", urls=[url]
    )
    snapshot([site], tmp_path, fetch_fn=fake_fetch, render_fn=fake_render)
    store = CorpusStore(tmp_path)
    links = "".join(f'<a href="{url}#{a}">x</a>' for a in ("gone", "tiny", "real"))

    key = task_key(_task([url]), links, store)
    assert isinstance(key, dict)
    assert (key["anchor"], key["url"]) == ("real", url)  # "tiny" has no 4-word unit
    assert task_key(_task(["https://other.test/"]), links, store) == (
        "page_not_in_task_urls"
    )
    assert task_key(_task([url]), "<p>no links</p>", store) == "no_anchor_link"
    monkeypatch.setattr("bench.anchor_qrels.MAX_SECTION_TOKENS", 3)
    assert task_key(_task([url]), links, store) == "section_too_broad"


def _task(urls: list[str]) -> ExternalTask:
    return ExternalTask(
        id="so-1",
        split="dev",
        site="python",
        task_type="fact",
        urls=urls,
        cited_urls=urls,
        question_title="t",
        question="q",
        reference_answer="a",
        expected_evidence=[],
        source=Source(
            question_url="https://stackoverflow.com/q/1",
            answer_url="https://stackoverflow.com/a/2",
            question_author="Ada",
            answer_author="Linus",
            license="CC BY-SA 4.0",
            created="2023-01-02",
        ),
    )


def test_tune_half_is_seeded_and_about_half() -> None:
    from bench.anchor_qrels import in_tune_half

    ids = [f"so-{n}" for n in range(4000)]
    picks = [in_tune_half(i) for i in ids]
    assert 0.47 < sum(picks) / len(picks) < 0.53
    assert picks == [in_tune_half(i) for i in ids]


def test_inline_css_and_scripts_are_not_section_text() -> None:
    html = (
        "<html><body><section><h2 id='c'>Compat</h2>"
        "<style>a{color:red}</style><script>var x = 1;</script>"
        "<p>Works in every browser since 2020.</p></section></body></html>"
    )
    assert section_text(html, "c") == "Compat\nWorks in every browser since 2020."
