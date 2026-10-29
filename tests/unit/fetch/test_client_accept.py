"""The optional ``Accept`` header of the guarded client (SPEC §3.5, M10b)."""

from __future__ import annotations

import httpx

from app.config import Settings
from app.fetch import client
from tests.unit.fetch._resolvers import FakeResolver

SETTINGS = Settings()
RESOLVER = FakeResolver({"public.example.com": ["93.184.216.34"]})


def _fetch_accepts(accept: str | None) -> list[str | None]:
    seen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("accept"))
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "/docs"})
        return httpx.Response(
            200, content=b"# hi", headers={"content-type": "text/markdown"}
        )

    client.fetch(
        "http://public.example.com/start",
        resolver=RESOLVER,
        transport=httpx.MockTransport(handler),
        settings=SETTINGS,
        accept=accept,
    )
    return seen


def test_default_sends_no_markdown_preference() -> None:
    # httpx's own default (*/*) — unchanged from before the option existed.
    assert _fetch_accepts(None) == ["*/*", "*/*"]


def test_accept_is_sent_on_every_hop() -> None:
    accept = "text/markdown, text/html;q=0.9"
    assert _fetch_accepts(accept) == [accept, accept]
