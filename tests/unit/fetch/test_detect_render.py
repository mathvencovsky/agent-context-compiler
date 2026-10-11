"""Pure render decision — should_render / _has_spa_signals (SPEC §3.3).

No network, no browser: these tests exercise only the string + config logic of
``app.fetch.detect.should_render``. They are intentionally NOT marked
``browser`` and never open a socket.
"""

from __future__ import annotations

import pytest

from app.config import Settings
from app.fetch import detect

# HTML fragments carrying exactly one SPA signal each (SPEC §3.3).
_EMPTY_ROOT = '<html><body><div id="root"></div></body></html>'
_EMPTY_APP = '<html><body><div id="app">  </div></body></html>'
_NEXT_DATA = (
    '<html><body><div id="__next"></div>'
    '<script id="__NEXT_DATA__" type="application/json">{}</script>'
    "</body></html>"
)
_NUXT = "<html><body><script>window.__NUXT__={state:{}}</script></body></html>"
_NOSCRIPT = (
    "<html><body><noscript>You need to enable JavaScript to run this app."
    "</noscript></body></html>"
)

_SPA_SIGNALS = [
    pytest.param(_EMPTY_ROOT, id="empty-div-root"),
    pytest.param(_EMPTY_APP, id="empty-div-app"),
    pytest.param(_NEXT_DATA, id="next-data"),
    pytest.param(_NUXT, id="window-nuxt"),
    pytest.param(_NOSCRIPT, id="noscript-needs-js"),
]

# A fully rendered page with substantial text and no SPA signal.
_RENDERED = (
    "<html><body><main>"
    + "<p>"
    + ("Real documentation content. " * 60)
    + "</p>"
    + "</main></body></html>"
)


@pytest.mark.parametrize("html", _SPA_SIGNALS)
def test_never_is_always_false(html: str) -> None:
    # 'never' wins even with an SPA signal AND short text.
    assert (
        detect.should_render(main_text_len=0, html_text=html, render="never") is False
    )


def test_always_is_always_true_even_with_long_text() -> None:
    # 'always' wins even with long text and no SPA signal.
    assert (
        detect.should_render(main_text_len=10_000, html_text=_RENDERED, render="always")
        is True
    )


@pytest.mark.parametrize("html", _SPA_SIGNALS)
def test_auto_true_when_short_text_and_spa_signal(html: str) -> None:
    # 'auto' renders only when BOTH conditions hold — short text + an SPA signal.
    assert (
        detect.should_render(
            main_text_len=10,
            html_text=html,
            render="auto",
            settings=Settings(),
        )
        is True
    )


@pytest.mark.parametrize("html", _SPA_SIGNALS)
def test_auto_false_when_text_is_long(html: str) -> None:
    # Long main text defeats 'auto' even if an SPA signal is present.
    assert (
        detect.should_render(
            main_text_len=10_000,
            html_text=html,
            render="auto",
            settings=Settings(),
        )
        is False
    )


def test_auto_false_when_no_spa_signal() -> None:
    # Short text alone is not enough without an SPA signal.
    assert (
        detect.should_render(
            main_text_len=0,
            html_text="<html><body><main><p>Hi.</p></main></body></html>",
            render="auto",
            settings=Settings(),
        )
        is False
    )


def test_auto_defaults_to_auto_mode() -> None:
    # render defaults to 'auto'.
    assert (
        detect.should_render(
            main_text_len=10, html_text=_EMPTY_ROOT, settings=Settings()
        )
        is True
    )


def test_threshold_comes_from_config() -> None:
    # The cut-off is settings.render_min_text_chars, not a hard-coded 500.
    low = Settings(render_min_text_chars=50)
    high = Settings(render_min_text_chars=200)
    # main_text_len=100 is below 200 but not below 50.
    assert (
        detect.should_render(main_text_len=100, html_text=_EMPTY_ROOT, settings=high)
        is True
    )
    assert (
        detect.should_render(main_text_len=100, html_text=_EMPTY_ROOT, settings=low)
        is False
    )


@pytest.mark.parametrize("html", _SPA_SIGNALS)
def test_has_spa_signals_detects_each_signal(html: str) -> None:
    assert detect._has_spa_signals(html) is True


def test_has_spa_signals_false_on_plain_page() -> None:
    assert (
        detect._has_spa_signals(
            "<html><body><main><p>Plenty of real text here.</p></main></body></html>"
        )
        is False
    )


def test_filled_root_container_is_not_a_signal() -> None:
    # A root/app container WITH rendered content is not an empty-mount signal.
    filled = '<html><body><div id="root"><h1>Rendered title</h1></div></body></html>'
    assert detect._has_spa_signals(filled) is False
