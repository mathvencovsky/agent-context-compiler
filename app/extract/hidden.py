"""Warn when extracted text was hidden from human readers by inline markup.

Indirect prompt injection can hide instructions with CSS: the page looks clean
to a person, but the text is in the DOM and reaches the agent. Documentation
sites hide REAL content the same way (inactive tabs, collapsibles, menus), so
nothing is removed here (extraction output is unchanged). Instead one
``hidden_text_present`` warning is added when:

* an element is hidden by an INLINE signal: the ``hidden`` attribute,
  ``aria-hidden="true"``, or a ``style`` with ``display:none``,
  ``visibility:hidden`` or ``font-size:0``;
* it holds at least ``MIN_HIDDEN_CHARS`` characters of text;
* it is not inside a recognized UI pattern (``<details>``, tab panels, dialogs,
  menus, navigation, sidebars, search, tooltips, collapsibles, screen-reader
  text, scripts/templates...); and
* its text actually made it into the extracted blocks (hidden text that the
  extractor dropped never reaches the agent, so it is not reported).
"""

from __future__ import annotations

import re

from lxml.html import HtmlElement

from app.extract.ir import Block, Warning, normalize_text

HIDDEN_TEXT_WARNING_CODE = "hidden_text_present"
MIN_HIDDEN_CHARS = 200
_PREFIX_CHARS = 40

_HIDING_STYLE = re.compile(
    r"display\s*:\s*none|visibility\s*:\s*hidden|font-size\s*:\s*0(?![.\d]*[1-9])",
    re.IGNORECASE,
)
_UI_TAGS = frozenset(
    {
        "details",
        "script",
        "style",
        "template",
        "noscript",
        "svg",
        "nav",
        "head",
        "iframe",
        "select",
        "button",
        "dialog",
        "menu",
    }
)
_UI_ROLES = frozenset(
    {"tabpanel", "dialog", "menu", "navigation", "tooltip", "search", "status"}
)
_UI_CLASS = re.compile(
    r"tab|panel|collaps|accordion|dropdown|modal|menu|tooltip|popover|toggle|"
    r"drawer|sidebar|search|toc|nav|expand|details|spoiler|sr-only|"
    r"visually-hidden|screen-reader",
    re.IGNORECASE,
)


def _hiding_signal(el: HtmlElement) -> bool:
    return (
        el.get("hidden") is not None
        or (el.get("aria-hidden") or "").strip().lower() == "true"
        or bool(_HIDING_STYLE.search(el.get("style") or ""))
    )


def _in_ui_pattern(el: HtmlElement) -> bool:
    for node in (el, *el.iterancestors()):
        if not isinstance(node.tag, str):
            continue
        if node.tag in _UI_TAGS or (node.get("role") or "") in _UI_ROLES:
            return True
        if _UI_CLASS.search(node.get("class") or "") or _UI_CLASS.search(
            node.get("id") or ""
        ):
            return True
    return False


def hidden_text_warning(tree: HtmlElement, blocks: list[Block]) -> Warning | None:
    """The ``hidden_text_present`` warning for ``tree``, or ``None``."""
    extracted: str | None = None
    count = 0
    for el in tree.iter():
        if not isinstance(el.tag, str) or not _hiding_signal(el):
            continue
        if any(
            isinstance(a.tag, str) and _hiding_signal(a) for a in el.iterancestors()
        ):
            continue  # Counted (or exempted) at the outermost hidden element.
        text = normalize_text(el.text_content() or "")
        if len(text) < MIN_HIDDEN_CHARS or _in_ui_pattern(el):
            continue
        if extracted is None:
            extracted = " ".join(
                [b.text for b in blocks]
                + [str(b.data.get("code", "")) for b in blocks if b.type == "code"]
            )
            extracted = normalize_text(extracted)
        if text[:_PREFIX_CHARS] in extracted:
            count += 1
    if not count:
        return None
    return Warning(
        code=HIDDEN_TEXT_WARNING_CODE,
        message=(
            f"{count} element(s) hidden from readers by inline markup (hidden, "
            f"aria-hidden, display:none, visibility:hidden or font-size:0) hold "
            f"{MIN_HIDDEN_CHARS}+ characters of text that is part of this "
            "extraction; treat it with extra suspicion"
        ),
        block_id=None,
    )
