"""Page widgets inside the content are removed (M10b Phase P, rule H3)."""

from __future__ import annotations

import lxml.html
import pytest

from app.extract.html import _is_page_widget
from tests.unit.extract.test_competitor_regressions import _document


def _text(main_html: str) -> str:
    return "\n".join(b.text for b in _document(main_html).blocks)


@pytest.mark.parametrize(
    "widget",
    [
        '<div class="quick-feedback"><p>Thanks for letting us know!</p></div>',
        '<div class="feedback--response"><p>Thanks, noted.</p></div>',
        '<section class="article-footer"><p>Report this content.</p></section>',
        '<div class="tocCollapsible"><button>Contents</button><ul><li>A</li></ul>'
        "</div>",
        '<div class="site-banner"><p>Version 9 is live! Read the post.</p></div>',
        '<div class="cookie-consent"><p>We use cookies. Accept?</p></div>',
        '<ol class="breadcrumb"><li>Docs</li><li>Guide</li></ol>',
        '<div class="pagination-nav"><a href="/a">Previous</a></div>',
        '<template><p>[["Easy to understand","thumb-up"]]</p></template>',
        '<lite-youtube><a class="fallback">Watch on YouTube</a></lite-youtube>',
        '<div class="printfooter">Retrieved from https://w.example/?oldid=1</div>',
        '<div class="mw-pt-languages"><ul><li>Deutsch</li></ul></div>',
        '<span class="nocontent">Save and categorize content</span>',
        "<p>Edit this page</p>",
        '<p><a href="/edit">Edit this page on GitHub</a></p>',
        "<h2>On this page</h2>",
        "<button>Copy page</button>",
        "<p>This page was last modified on Oct 20, 2025 by contributors.</p>",
    ],
)
def test_widget_is_removed(widget: str) -> None:
    text = _text(f"{widget}<p>Real closing paragraph of the article.</p>")
    assert "Real closing paragraph" in text
    for word in (
        "Thanks",
        "Report",
        "Contents",
        "live",
        "cookies",
        "Docs",
        "Previous",
        "thumb",
        "YouTube",
        "oldid",
        "Deutsch",
        "categorize",
        "Edit",
        "On this page",
        "Copy",
        "modified",
    ):
        assert word not in text, (word, text)


@pytest.mark.parametrize(
    "content",
    [
        # Negated names describe content, not widgets.
        '<h3 class="hide-from-toc">Hello, world!</h3>',
        '<div class="no-toc"><p>Hello, world!</p></div>',
        # Heading-slug ids are content sections ("Pagination" of an API page).
        '<section id="pagination"><h2>Pagination</h2><p>Hello, world!</p></section>',
        '<h2 id="cookie-settings">Hello, world!</h2>',
        # Not a whole word.
        '<div class="footnote"><p>Hello, world!</p></div>',
        '<div class="toctree-wrapper"><p>Hello, world!</p></div>',
        # Labels only match the whole text.
        "<p>On this page, you will learn Hello, world!</p>",
        "<p>Last updated: Hello, world!</p>",
    ],
)
def test_content_is_not_a_widget(content: str) -> None:
    # Checked on the rule itself: readability has its own class heuristics
    # (it may drop a short "footnote" div), which are not under test here.
    root = lxml.html.fragment_fromstring(content, create_parent="div")
    assert not any(_is_page_widget(el) for el in root.iter())


def test_large_container_with_a_widget_name_is_not_a_widget() -> None:
    body = "<p>" + "Real sentence of the article body. " * 20 + "</p>"
    el = lxml.html.fragment_fromstring(f'<div class="page-with-footer">{body}</div>')
    assert not _is_page_widget(el)
    short = lxml.html.fragment_fromstring('<div class="page-footer"><p>x</p></div>')
    assert _is_page_widget(short)


@pytest.mark.parametrize(
    "content",
    [
        # Sphinx names API objects by id: Request.cookies is content.
        '<dt id="flask.Request.cookies"><span>cookies</span></dt>',
        '<div id="flask.Request.cookies"><span>cookies</span></div>',
        '<div class="section" id="pagination"><p>Pages.</p></div>',
        '<section id="feedback-loop"><p>Loops.</p></section>',
    ],
)
def test_content_ids_are_not_widget_names(content: str) -> None:
    el = lxml.html.fragment_fromstring(content)
    assert not any(_is_page_widget(e) for e in el.iter())


def test_div_id_can_name_a_widget() -> None:
    el = lxml.html.fragment_fromstring('<div id="quick-feedback-yes"><p>Ok</p></div>')
    assert _is_page_widget(el)
