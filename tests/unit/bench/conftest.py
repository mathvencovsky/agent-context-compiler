"""No-network guard and small-corpus fixtures for the bench suite.

The M2 harness tests must never touch the network and never spend API keys
(CLAUDE.md / context.json): the LLM provider is the deterministic fake, the
fetcher/renderer are fakes, and the corpus is a tiny hand-built fixture. The
autouse ``_no_network`` fixture mirrors ``tests/unit/fetch/conftest.py`` so any
accidental outbound connection fails loudly.
"""

from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest

from bench.corpus_store import CorpusStore


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail any bench test that opens a network connection beyond loopback.

    Loopback stays open because asyncio's event loop on Windows builds its
    self-pipe from a loopback socket pair.
    """

    def _blocked(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("real network access attempted in a test")

    connect = socket.socket.connect

    def _loopback_only(sock: socket.socket, address: object) -> None:
        if isinstance(address, tuple) and address[0] in ("127.0.0.1", "::1"):
            return connect(sock, address)
        raise AssertionError("real network access attempted in a test")

    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(socket, "getaddrinfo", _blocked)
    monkeypatch.setattr(socket.socket, "connect", _loopback_only, raising=True)


#: Two small self-contained HTML documents used as the frozen corpus. The quotes
#: drafted/graded in the tests are literal substrings of this text.
_DOC_API = (
    "<html><body><main>"
    "<h1>abs(x) built-in</h1>"
    "<p>Return the absolute value of a number. The argument may be an "
    "integer, a floating point number, or an object implementing __abs__.</p>"
    "</main></body></html>"
)
_DOC_GUIDE = (
    "<html><body><main>"
    "<h1>Quickstart</h1>"
    "<p>A minimal application fits in a single file. First import the class, "
    "then create an instance and register a route to return a greeting.</p>"
    "</main></body></html>"
)


@pytest.fixture
def corpus_dir(tmp_path: Path) -> Path:
    """Write a tiny two-document corpus with a manifest and return its path."""
    root = tmp_path / "corpus"
    (root / "python").mkdir(parents=True)
    (root / "flask").mkdir(parents=True)

    api_bytes = _DOC_API.encode("utf-8")
    guide_bytes = _DOC_GUIDE.encode("utf-8")
    import hashlib

    api_hash = hashlib.sha256(api_bytes).hexdigest()
    guide_hash = hashlib.sha256(guide_bytes).hexdigest()
    (root / "python" / f"{api_hash}.html").write_bytes(api_bytes)
    (root / "flask" / f"{guide_hash}.html").write_bytes(guide_bytes)

    manifest = {
        "created_at": "2026-01-01T00:00:00+00:00",
        "entries": [
            {
                "site": "Python Documentation",
                "site_key": "python",
                "rich_categories": ["api_reference", "guide"],
                "url": "https://docs.python.org/3/library/functions.html",
                "final_url": "https://docs.python.org/3/library/functions.html",
                "status": "ok",
                "reason": None,
                "content_type": "text/html",
                "content_hash": api_hash,
                "fetched_at": "2026-01-01T00:00:00+00:00",
                "raw_path": f"python/{api_hash}.html",
                "rendered_path": None,
            },
            {
                "site": "Flask",
                "site_key": "flask",
                "rich_categories": ["guide", "tutorial"],
                "url": "https://flask.palletsprojects.com/en/stable/quickstart/",
                "final_url": "https://flask.palletsprojects.com/en/stable/quickstart/",
                "status": "ok",
                "reason": None,
                "content_type": "text/html",
                "content_hash": guide_hash,
                "fetched_at": "2026-01-01T00:00:00+00:00",
                "raw_path": f"flask/{guide_hash}.html",
                "rendered_path": None,
            },
        ],
    }
    (root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return root


@pytest.fixture
def store(corpus_dir: Path) -> CorpusStore:
    """A :class:`CorpusStore` over the fixture corpus."""
    return CorpusStore(corpus_dir)


#: Path to the committed acceptance fixtures under tests/fixtures/bench/.
_FIXTURE_ROOT = Path(__file__).resolve().parents[2] / "fixtures" / "bench"


@pytest.fixture
def fixture_corpus_dir() -> str:
    """Return the committed acceptance corpus directory (hand-built, offline)."""
    return str(_FIXTURE_ROOT / "corpus")


@pytest.fixture
def fixture_tasks_path() -> str:
    """Return the committed acceptance ``tasks.yaml`` path."""
    return str(_FIXTURE_ROOT / "tasks.yaml")
