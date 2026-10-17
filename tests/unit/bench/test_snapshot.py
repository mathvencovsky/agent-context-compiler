"""Snapshot with FAKE fetch/render: manifest shape, skip-on-robots, render rules.

No network: ``fetch_fn``/``render_fn`` are in-memory fakes and the autouse
``_no_network`` fixture would fail any real socket use.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from app.fetch.client import FetchResult
from app.fetch.errors import RobotsDisallowed
from app.fetch.render import RenderResult
from bench.corpus_store import CorpusStore
from bench.snapshot import SiteConfig, snapshot

# A real-looking HTML page with enough text that the auto heuristic does NOT
# render it (long main text, no SPA signal).
_RICH_HTML = (
    "<html><body><main><h1>Guide</h1><p>"
    + ("word " * 300)
    + "</p></main></body></html>"
)
# An empty SPA mount point with little text -> auto heuristic renders it.
_SPA_HTML = '<html><body><div id="root"></div></body></html>'


def _html_result(url: str, body: str) -> FetchResult:
    return FetchResult(
        final_url=url,
        status=200,
        headers=httpx.Headers({"content-type": "text/html; charset=utf-8"}),
        body=body.encode("utf-8"),
        content_type="text/html",
        redirects=0,
        elapsed_ms=1,
    )


@pytest.fixture
def sites() -> list[SiteConfig]:
    return [
        SiteConfig(
            site="Guide Site",
            key="guide",
            rich_categories=["guide"],
            render="auto",
            urls=[
                "https://example.test/ok",
                "https://example.test/blocked",
            ],
        ),
        SiteConfig(
            site="SPA Site",
            key="spa",
            rich_categories=["spa_js", "guide"],
            render="always",
            urls=["https://spa.test/app"],
        ),
    ]


def test_snapshot_writes_manifest_with_ok_and_skipped(
    sites: list[SiteConfig], tmp_path: Path
) -> None:
    rendered_urls: list[str] = []

    def fake_fetch(url: str) -> FetchResult:
        if url.endswith("/blocked"):
            raise RobotsDisallowed("robots.txt forbids this path", details={"url": url})
        if url.startswith("https://spa.test"):
            return _html_result(url, _SPA_HTML)
        return _html_result(url, _RICH_HTML)

    def fake_render(url: str) -> RenderResult:
        rendered_urls.append(url)
        return RenderResult(final_url=url, html="<html>rendered</html>", status=200)

    corpus = tmp_path / "corpus"
    manifest = snapshot(sites, corpus, fetch_fn=fake_fetch, render_fn=fake_render)

    by_url = {e["url"]: e for e in manifest["entries"]}

    # The robots-blocked URL is skipped with a reason, not aborting the rest.
    blocked = by_url["https://example.test/blocked"]
    assert blocked["status"] == "skipped"
    assert "robots_disallowed" in blocked["reason"]
    assert blocked["raw_path"] == ""

    # The plain OK auto page is stored and NOT rendered (long text, no SPA).
    ok = by_url["https://example.test/ok"]
    assert ok["status"] == "ok"
    assert ok["rendered_path"] is None
    assert (corpus / ok["raw_path"]).read_bytes() == _RICH_HTML.encode("utf-8")

    # The render:always SPA page is stored AND rendered.
    spa = by_url["https://spa.test/app"]
    assert spa["status"] == "ok"
    assert spa["rendered_path"] is not None
    assert (corpus / spa["rendered_path"]).read_text(encoding="utf-8") == (
        "<html>rendered</html>"
    )
    assert rendered_urls == ["https://spa.test/app"]

    # The manifest round-trips through the read-side store.
    store = CorpusStore(corpus)
    assert store.content_type("https://example.test/ok") == "text/html"
    assert store.rendered_html("https://spa.test/app") == "<html>rendered</html>"


def test_snapshot_auto_renders_spa_without_render_always(tmp_path: Path) -> None:
    sites = [
        SiteConfig(
            site="Auto SPA",
            key="auto",
            rich_categories=["guide"],
            render="auto",
            urls=["https://auto.test/app"],
        )
    ]
    rendered: list[str] = []

    def fake_fetch(url: str) -> FetchResult:
        return _html_result(url, _SPA_HTML)

    def fake_render(url: str) -> RenderResult:
        rendered.append(url)
        return RenderResult(final_url=url, html="<html>spa</html>", status=200)

    manifest = snapshot(
        sites, tmp_path / "c", fetch_fn=fake_fetch, render_fn=fake_render
    )
    entry = manifest["entries"][0]
    assert entry["rendered_path"] is not None
    assert rendered == ["https://auto.test/app"]


def test_snapshot_append_keeps_frozen_entries_and_fetches_only_new_urls(
    sites: list[SiteConfig], tmp_path: Path
) -> None:
    fetched: list[str] = []

    def fake_fetch(url: str) -> FetchResult:
        fetched.append(url)
        if url.endswith("/blocked"):
            raise RobotsDisallowed("robots.txt forbids this path", details={"url": url})
        return _html_result(url, _RICH_HTML)

    def fake_render(url: str) -> RenderResult:
        return RenderResult(final_url=url, html="<html>rendered</html>", status=200)

    corpus = tmp_path / "corpus"
    first = snapshot(sites[:1], corpus, fetch_fn=fake_fetch, render_fn=fake_render)
    fetched.clear()

    more = SiteConfig(
        site="Guide Site",
        key="guide",
        rich_categories=["guide"],
        render="auto",
        # ok/blocked are already frozen (blocked as skipped); only /new is fetched.
        urls=[
            "https://example.test/ok",
            "https://example.test/blocked",
            "https://example.test/new",
        ],
    )
    second = snapshot(
        [more], corpus, fetch_fn=fake_fetch, render_fn=fake_render, append=True
    )

    # robots.txt is read first (fresh checker), then only the new page.
    assert fetched == ["https://example.test/robots.txt", "https://example.test/new"]
    assert second["created_at"] == first["created_at"]
    assert second["entries"][:2] == first["entries"]
    assert second["entries"][2]["url"] == "https://example.test/new"
    assert CorpusStore(corpus).content_type("https://example.test/new") == "text/html"


def test_snapshot_respects_robots_txt_and_skips_error_pages(
    sites: list[SiteConfig], tmp_path: Path
) -> None:
    """A Disallow in robots.txt and a 404 page are both skipped, never stored."""
    fetched: list[str] = []

    def fake_fetch(url: str) -> FetchResult:
        fetched.append(url)
        if url == "https://example.test/robots.txt":
            return FetchResult(
                final_url=url,
                status=200,
                headers=httpx.Headers({"content-type": "text/plain"}),
                body=b"User-agent: *\nDisallow: /blocked\n",
                content_type="text/plain",
                redirects=0,
                elapsed_ms=1,
            )
        if url == "https://spa.test/robots.txt":
            return _html_result(url, "")
        if url.endswith("/app"):
            return FetchResult(
                final_url=url,
                status=404,
                headers=httpx.Headers({"content-type": "text/html"}),
                body=b"<html><body><h1>Page not found</h1></body></html>",
                content_type="text/html",
                redirects=0,
                elapsed_ms=1,
            )
        return _html_result(url, _RICH_HTML)

    def fake_render(url: str) -> RenderResult:
        raise AssertionError("nothing here should be rendered")

    corpus = tmp_path / "corpus"
    manifest = snapshot(sites, corpus, fetch_fn=fake_fetch, render_fn=fake_render)
    by_url = {e["url"]: e for e in manifest["entries"]}

    blocked = by_url["https://example.test/blocked"]
    assert blocked["status"] == "skipped" and "robots_disallowed" in blocked["reason"]
    assert "https://example.test/blocked" not in fetched  # never downloaded
    missing = by_url["https://spa.test/app"]
    assert missing["status"] == "skipped" and "upstream_status" in missing["reason"]
    assert missing["raw_path"] == ""
    assert by_url["https://example.test/ok"]["status"] == "ok"


def test_disallowed_now_lists_frozen_urls_robots_refuses_today() -> None:
    from app.fetch.robots import RobotsChecker
    from bench.snapshot import disallowed_now

    def fake_fetch(url: str) -> FetchResult:
        return FetchResult(
            final_url=url,
            status=200,
            headers=httpx.Headers({"content-type": "text/plain"}),
            body=b"User-agent: *\nDisallow: /old/\n",
            content_type="text/plain",
            redirects=0,
            elapsed_ms=1,
        )

    entries = [
        {"url": "https://example.test/old/page", "status": "ok"},
        {"url": "https://example.test/new/page", "status": "ok"},
        {"url": "https://example.test/old/skipped", "status": "skipped"},
    ]
    refused = disallowed_now(entries, RobotsChecker(fetch_fn=fake_fetch))
    assert refused == [("https://example.test/old/page", "robots_disallowed")]
