"""Freeze the benchmark corpus from ``bench/sites.yaml`` (SPEC §10.1 / §3).

``snapshot.py`` is the WRITE side of the frozen-corpus contract. For every URL
in ``bench/sites.yaml`` it:

1. fetches the URL through the M1 secure client (``app.fetch.client.fetch`` —
   guard + robots.txt), so a robots-disallowed or unreachable URL is SKIPPED
   (recorded with ``status="skipped"`` and a ``reason``) without aborting the
   rest of the snapshot;
2. decides whether a browser render is needed — ``render: always`` from the
   site config, or the M4 heuristic ``app.fetch.detect.should_render`` on an
   ``auto`` site — and if so renders it with ``app.fetch.render.render_url``;
3. writes the raw body to ``bench/corpus/<site_key>/<hash>.<ext>`` and any
   rendered HTML to ``…/<hash>.rendered.html``;
4. finally writes ``bench/corpus/manifest.json`` describing every entry.

``fetch_fn`` and ``render_fn`` are injectable so the tests drive the whole flow
with deterministic fakes and never touch the network (CLAUDE.md). ``httpx`` is
NOT imported here — all outbound traffic goes through ``app.fetch``.

This module is NOT run as part of the test suite: the real snapshot reaches the
public web. :func:`main` is the human entry point.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from app.fetch.client import FetchResult, fetch
from app.fetch.detect import RenderDecision, parse_content_type, should_render
from app.fetch.errors import FetchError
from app.fetch.render import RenderResult, render_url

#: Default corpus directory (gitignored; written by the human snapshot run).
DEFAULT_CORPUS_DIR = Path(__file__).resolve().parent / "corpus"
#: Default sites configuration consumed by the snapshot.
DEFAULT_SITES_PATH = Path(__file__).resolve().parent / "sites.yaml"

#: Injectable seams. The real functions reach the network; tests pass fakes.
FetchFn = Callable[[str], FetchResult]
RenderFn = Callable[[str], RenderResult]

#: Map a media type to the raw-file extension used in the corpus.
_EXT_BY_MEDIA_TYPE = {
    "application/pdf": "pdf",
    "text/html": "html",
    "application/xhtml+xml": "html",
}

#: Strip tags crudely to approximate the main-text length for the ``auto``
#: render heuristic. The real extraction (``app.extract.html``) is heavier; for
#: the snapshot's render decision an approximate text length is sufficient and
#: deterministic.
_TAG_RE = re.compile(r"<[^>]+>")


class SiteConfig:
    """One site block from ``sites.yaml`` (the fields the snapshot needs)."""

    def __init__(
        self,
        *,
        site: str,
        key: str,
        rich_categories: list[str],
        render: RenderDecision,
        urls: list[str],
    ) -> None:
        self.site = site
        self.key = key
        self.rich_categories = rich_categories
        self.render = render
        self.urls = urls


def load_sites(path: str | Path = DEFAULT_SITES_PATH) -> list[SiteConfig]:
    """Parse ``sites.yaml`` into a list of :class:`SiteConfig`."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    sites: list[SiteConfig] = []
    for raw in data["sites"]:
        sites.append(
            SiteConfig(
                site=raw["site"],
                key=raw["key"],
                rich_categories=list(raw.get("categories", [])),
                render=raw.get("render", "auto"),
                urls=list(raw["urls"]),
            )
        )
    return sites


def _raw_extension(content_type: str) -> str:
    """Return the file extension for ``content_type`` (defaults to ``bin``)."""
    return _EXT_BY_MEDIA_TYPE.get(content_type, "bin")


def _approx_main_text_len(html_text: str) -> int:
    """Approximate the main-text length by stripping tags (auto heuristic only)."""
    return len(_TAG_RE.sub(" ", html_text).strip())


def _needs_render(
    *,
    render: RenderDecision,
    content_type: str,
    html_text: str,
) -> bool:
    """Decide whether to render, reusing the M4 ``should_render`` for ``auto``."""
    if render == "always":
        return True
    if render == "never":
        return False
    # auto: only HTML can be a SPA; PDFs and other types never render.
    if content_type not in ("text/html", "application/xhtml+xml"):
        return False
    return should_render(
        main_text_len=_approx_main_text_len(html_text),
        html_text=html_text,
        render="auto",
    )


def _decode_html(result: FetchResult) -> str:
    """Decode an HTML body to text, honoring the Content-Type charset."""
    _media, charset = parse_content_type(result.headers.get("content-type"))
    return result.body.decode(charset or "utf-8", errors="replace")


def snapshot(
    sites: list[SiteConfig],
    corpus_dir: str | Path,
    *,
    fetch_fn: FetchFn = fetch,
    render_fn: RenderFn = render_url,
    append: bool = False,
) -> dict[str, Any]:
    """Freeze every site URL into ``corpus_dir`` and write ``manifest.json``.

    Each URL is fetched through ``fetch_fn`` (the guarded M1 client by default).
    A :class:`~app.fetch.errors.FetchError` (robots disallow, timeout, blocked
    destination, …) SKIPS that URL with a recorded reason and does not abort the
    rest. HTML on a ``render: always`` site or an ``auto`` site that trips
    ``should_render`` is also rendered through ``render_fn``; a render failure is
    non-fatal (the raw entry is still ``ok``, ``rendered_path`` stays ``None``).

    With ``append=True`` an existing ``manifest.json`` is kept as is and only
    URLs it does not list yet are fetched (M10 adds cited pages this way), so
    the frozen corpus under earlier tasks never changes. A URL already listed,
    even as ``skipped``, is not fetched again.

    Returns the manifest as a plain dict (also written to disk) so a caller/test
    can assert on it without re-reading the file.
    """
    corpus_dir = Path(corpus_dir)
    corpus_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = corpus_dir / "manifest.json"

    manifest: dict[str, Any] = {
        "created_at": datetime.now(UTC).isoformat(),
        "entries": [],
    }
    if append and manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    known = {entry["url"] for entry in manifest["entries"]}
    for site in sites:
        site_dir = corpus_dir / site.key
        for url in site.urls:
            if url in known:
                continue
            known.add(url)
            manifest["entries"].append(
                _snapshot_url(
                    site=site,
                    url=url,
                    site_dir=site_dir,
                    corpus_dir=corpus_dir,
                    fetch_fn=fetch_fn,
                    render_fn=render_fn,
                )
            )

    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return manifest


def _snapshot_url(
    *,
    site: SiteConfig,
    url: str,
    site_dir: Path,
    corpus_dir: Path,
    fetch_fn: FetchFn,
    render_fn: RenderFn,
) -> dict[str, Any]:
    """Fetch (and maybe render) one URL, returning its manifest entry dict."""
    base: dict[str, Any] = {
        "site": site.site,
        "site_key": site.key,
        "rich_categories": site.rich_categories,
        "url": url,
    }

    try:
        result = fetch_fn(url)
    except FetchError as exc:
        return {
            **base,
            "final_url": url,
            "status": "skipped",
            "reason": f"{exc.code}: {exc.message}",
            "content_type": "",
            "content_hash": "",
            "fetched_at": datetime.now(UTC).isoformat(),
            "raw_path": "",
            "rendered_path": None,
        }

    content_hash = hashlib.sha256(result.body).hexdigest()
    ext = _raw_extension(result.content_type)
    site_dir.mkdir(parents=True, exist_ok=True)
    raw_rel = f"{site.key}/{content_hash}.{ext}"
    (corpus_dir / raw_rel).write_bytes(result.body)

    rendered_rel: str | None = None
    html_text = ""
    if result.content_type in ("text/html", "application/xhtml+xml"):
        html_text = _decode_html(result)

    if _needs_render(
        render=site.render,
        content_type=result.content_type,
        html_text=html_text,
    ):
        try:
            rendered = render_fn(url)
        except FetchError:
            rendered = None
        if rendered is not None:
            rendered_rel = f"{site.key}/{content_hash}.rendered.html"
            (corpus_dir / rendered_rel).write_text(rendered.html, encoding="utf-8")

    return {
        **base,
        "final_url": result.final_url,
        "status": "ok",
        "reason": None,
        "content_type": result.content_type,
        "content_hash": content_hash,
        "fetched_at": datetime.now(UTC).isoformat(),
        "raw_path": raw_rel,
        "rendered_path": rendered_rel,
    }


def main() -> None:
    """Human entry point: snapshot every site in ``sites.yaml`` into the corpus.

    Reaches the public web; NOT run by the test suite.
    """
    sites = load_sites()
    manifest = snapshot(sites, DEFAULT_CORPUS_DIR)
    ok = sum(1 for e in manifest["entries"] if e["status"] == "ok")
    skipped = len(manifest["entries"]) - ok
    print(f"snapshot complete: {ok} ok, {skipped} skipped -> {DEFAULT_CORPUS_DIR}")


if __name__ == "__main__":
    main()
