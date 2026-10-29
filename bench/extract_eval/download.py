"""Checksum-verified downloads of pinned benchmark data from GitHub.

This is a dev-only data fetcher, not product code: every URL is built from
constants in this package (a pinned commit or release tag on GitHub), never
from user input, so there is no SSRF surface for ``app/fetch/guard.py`` to
cover (see DECISIONS.md 2026-10-28, M10b). It still fails closed:

- only ``https`` to an allowlisted GitHub host, before and after redirects;
- a hard size cap per file;
- the sha256 must match the committed manifest, or the file is not written.

It uses the standard library (``urllib``), which honours ``HTTPS_PROXY`` and
the system CA bundle, so it runs unchanged behind a corporate proxy.
"""

from __future__ import annotations

import hashlib
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

#: Hosts a download may start at or be redirected to.
ALLOWED_HOSTS = frozenset(
    {
        "raw.githubusercontent.com",
        "github.com",
        "release-assets.githubusercontent.com",
        "objects.githubusercontent.com",
    }
)
MAX_BYTES = 16 * 1024 * 1024
TIMEOUT_S = 60
USER_AGENT = "NINA-bench-extract-eval/1.0 (+benchmark data fetch)"


class DownloadError(RuntimeError):
    """A download was refused, failed or did not match its checksum."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def check_url(url: str) -> None:
    """Refuse anything that is not https to an allowlisted GitHub host."""
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname not in ALLOWED_HOSTS:
        raise DownloadError(f"refused URL outside the GitHub allowlist: {url}")
    if parts.port not in (None, 443) or parts.username or parts.password:
        raise DownloadError(f"refused URL with port or credentials: {url}")


class _AllowlistRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(  # type: ignore[no-untyped-def]
        self, req, fp, code, msg, headers, newurl
    ):
        check_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_bytes(url: str, *, max_bytes: int = MAX_BYTES) -> bytes:
    """GET ``url`` (allowlisted, size-capped) and return the body."""
    check_url(url)
    opener = urllib.request.build_opener(_AllowlistRedirects())
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with opener.open(request, timeout=TIMEOUT_S) as response:
        check_url(response.geturl())
        body: bytes = response.read(max_bytes + 1)
    if len(body) > max_bytes:
        raise DownloadError(f"{url} is larger than {max_bytes} bytes")
    return body


def ensure_file(url: str, dest: Path, expected_sha256: str) -> bool:
    """Make ``dest`` hold the verified file; return True when it downloaded.

    An existing file with the right checksum is kept (no request). A wrong
    checksum, local or downloaded, raises and leaves nothing new on disk.
    """
    if dest.exists():
        if sha256_bytes(dest.read_bytes()) == expected_sha256:
            return False
        dest.unlink()
    body = fetch_bytes(url)
    got = sha256_bytes(body)
    if got != expected_sha256:
        raise DownloadError(
            f"checksum mismatch for {url}: expected {expected_sha256}, got {got}"
        )
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    tmp.write_bytes(body)
    tmp.replace(dest)
    return True
