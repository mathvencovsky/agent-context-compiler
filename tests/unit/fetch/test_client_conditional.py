"""Conditional request validators of the guarded client (SPEC §5, M10b).

``If-None-Match`` / ``If-Modified-Since`` are the only headers a caller can add
besides ``Accept``; their values come from a stored upstream response, so they
are re-checked (printable ASCII, bounded) before they are sent back out.
"""

from __future__ import annotations

import httpx
import pytest

from app.config import Settings
from app.fetch import client
from tests.unit.fetch._resolvers import FakeResolver

SETTINGS = Settings()
RESOLVER = FakeResolver({"public.example.com": ["93.184.216.34"]})


def _seen(**validators: str | None) -> list[tuple[str | None, str | None]]:
    seen: list[tuple[str | None, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(
            (
                request.headers.get("if-none-match"),
                request.headers.get("if-modified-since"),
            )
        )
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "/docs"})
        return httpx.Response(304)

    result = client.fetch(
        "http://public.example.com/start",
        resolver=RESOLVER,
        transport=httpx.MockTransport(handler),
        settings=SETTINGS,
        **validators,  # type: ignore[arg-type]
    )
    assert result.status == 304
    return seen


def test_no_validators_by_default() -> None:
    assert _seen() == [(None, None), (None, None)]


def test_validators_are_sent_on_every_hop() -> None:
    lm = "Tue, 27 Oct 2026 10:00:00 GMT"
    assert _seen(if_none_match='W/"abc"', if_modified_since=lm) == [
        ('W/"abc"', lm),
        ('W/"abc"', lm),
    ]


@pytest.mark.parametrize(
    "value",
    [
        '"a"\r\nX-Injected: 1',
        '"a"\n',
        '"café"',
        "\x00",
        "",
        '"' + "a" * 2000 + '"',
    ],
)
def test_unsafe_validators_are_dropped(value: str) -> None:
    assert _seen(if_none_match=value, if_modified_since=value) == [
        (None, None),
        (None, None),
    ]
