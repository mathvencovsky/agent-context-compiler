"""Integration-test harness for the resolve/get-document endpoints — M5.

Every fixture here wires a FastAPI :class:`TestClient` whose dependency seams
(see ``app/api/documents.py``) are overridden so the resolve path runs against:

* an **instrumented** ``httpx.MockTransport`` — a scripted handler with a call
  counter and a per-host response script, which ALSO serves ``/robots.txt``.
  HTTP therefore NEVER touches the internet (CLAUDE.md non-negotiable); the
  counter lets a test assert that a cache hit performs zero network calls.
* a :class:`FakeResolver` mapping the fixture host to a fixed public IP, so the
  SSRF guard passes without any real DNS.
* a **real** :class:`BlobStore` against the local SeaweedFS S3 gateway.
* a session factory bound to the **real** test Postgres (compose service).

Alembic migrations are applied once per session; the two M5 tables are
truncated between tests so each case starts clean. The module is marked
``integration`` so the service-free fast gate skips it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.api import documents
from app.config import Settings, get_settings
from app.fetch.client import fetch as guarded_fetch
from app.fetch.guard import Resolver
from app.fetch.robots import RobotsChecker
from app.main import create_app
from app.store.blobs import BlobStore
from app.store.repo import make_engine, make_session_factory
from tests.unit.fetch._resolvers import FakeResolver

pytestmark = pytest.mark.integration

#: The host every fixture resolves against; mapped to a globally-routable
#: public IP so the SSRF guard accepts it with no real DNS. (Documentation
#: ranges like TEST-NET are NOT ``is_global`` and would be blocked, so a real
#: public literal is used — no connection is ever opened against it, the HTTP
#: transport is fully mocked.)
FIXTURE_HOST = "docs.example.com"
FIXTURE_PUBLIC_IP = "93.184.216.34"

#: A host that resolves to a private IP, to exercise the SSRF guard
#: (blocked_destination) without any real DNS.
BLOCKED_HOST = "internal.example.com"
BLOCKED_PRIVATE_IP = "10.0.0.1"


# --------------------------------------------------------------------------- #
# Instrumented transport
# --------------------------------------------------------------------------- #
@dataclass
class ScriptedTransport:
    """An ``httpx.MockTransport`` wrapper with a call counter and a URL script.

    ``script`` maps a request path (e.g. ``/guide``) to a zero-argument factory
    returning an :class:`httpx.Response`, or to a callable raising a transport
    error to exercise the fetch_failed/timeout branches. ``/robots.txt`` is
    served from ``robots_body`` (allow-all by default). ``calls`` counts every
    request routed through the transport, so a test can assert a cache hit did
    not touch the network.
    """

    script: dict[str, Callable[[], httpx.Response]] = field(default_factory=dict)
    robots_body: bytes = b"User-agent: *\nAllow: /\n"
    calls: int = 0
    paths: list[str] = field(default_factory=list)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        path = request.url.path
        self.paths.append(path)
        if path == "/robots.txt":
            return httpx.Response(
                200,
                content=self.robots_body,
                headers={"content-type": "text/plain"},
            )
        factory = self.script.get(path)
        if factory is None:
            return httpx.Response(404, content=b"not found")
        return factory()

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def set(self, path: str, factory: Callable[[], httpx.Response]) -> None:
        self.script[path] = factory


def html_response(
    body: bytes, *, headers: dict[str, str] | None = None
) -> httpx.Response:
    """Build a 200 HTML response for the scripted transport."""
    merged = {"content-type": "text/html; charset=utf-8"}
    if headers:
        merged.update(headers)
    return httpx.Response(200, content=body, headers=merged)


# --------------------------------------------------------------------------- #
# Database: engine, migrations, per-test cleanup
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="session")
def settings() -> Settings:
    """Process settings (reads .env / compose defaults)."""
    return get_settings()


@pytest.fixture(scope="session")
def engine(settings: Settings) -> Engine:
    """A SQLAlchemy engine bound to the test Postgres, migrated to head."""
    eng = make_engine(settings)
    _apply_migrations()
    return eng


def _apply_migrations() -> None:
    """Apply Alembic migrations to head against the configured database."""
    from alembic import command
    from alembic.config import Config

    cfg = Config("alembic.ini")
    command.upgrade(cfg, "head")


@pytest.fixture(scope="session")
def session_factory(engine: Engine) -> sessionmaker[Session]:
    return make_session_factory(engine)


def _truncate(engine: Engine) -> None:
    with engine.begin() as conn:
        # chunks reference document_versions, so clear them first (M6).
        conn.execute(text("DELETE FROM chunks"))
        # Break the circular sources <-> document_versions FK before deleting.
        conn.execute(text("UPDATE sources SET current_version_id = NULL"))
        conn.execute(text("DELETE FROM document_versions"))
        conn.execute(text("DELETE FROM sources"))


# --------------------------------------------------------------------------- #
# Blob store (real SeaweedFS)
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="session")
def blob_store(settings: Settings) -> BlobStore:
    return BlobStore(settings)


# --------------------------------------------------------------------------- #
# Clock
# --------------------------------------------------------------------------- #
@dataclass
class FakeClock:
    """A controllable UTC clock for exercising the resolve cache TTL window."""

    now: datetime = field(default_factory=lambda: datetime(2026, 10, 11, tzinfo=UTC))

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now = self.now + timedelta(seconds=seconds)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


# --------------------------------------------------------------------------- #
# Resolver (fake, public IP)
# --------------------------------------------------------------------------- #
@pytest.fixture
def resolver() -> FakeResolver:
    return FakeResolver(
        {
            FIXTURE_HOST: [FIXTURE_PUBLIC_IP],
            BLOCKED_HOST: [BLOCKED_PRIVATE_IP],
        }
    )


# --------------------------------------------------------------------------- #
# Robots checker routed through the instrumented transport
# --------------------------------------------------------------------------- #
def _make_robots_checker(
    transport: ScriptedTransport,
    resolver: Resolver,
    settings: Settings,
) -> RobotsChecker:
    """A RobotsChecker whose fetch_fn goes through the guarded client + mock.

    ``/robots.txt`` is served by the instrumented transport, so robots lookups
    are mocked like every other request and count toward the call counter.
    """

    def fetch_fn(url: str) -> object:
        return guarded_fetch(
            url,
            resolver=resolver,
            transport=transport.transport,
            settings=settings,
        )

    # time_fn fixed so the per-domain robots cache never expires mid-test.
    return RobotsChecker(fetch_fn=fetch_fn, time_fn=lambda: 0.0, settings=settings)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# TestClient wired with all overrides
# --------------------------------------------------------------------------- #
@dataclass
class Harness:
    """Everything a resolve/get test needs, with the transport counter exposed."""

    client: TestClient
    transport: ScriptedTransport
    clock: FakeClock
    session_factory: sessionmaker[Session]
    blob_store: BlobStore
    resolver: FakeResolver
    settings: Settings
    base_url: str = f"http://{FIXTURE_HOST}"

    def url(self, path: str) -> str:
        return f"{self.base_url}{path}"


@pytest.fixture
def harness(
    engine: Engine,
    session_factory: sessionmaker[Session],
    blob_store: BlobStore,
    resolver: FakeResolver,
    clock: FakeClock,
    settings: Settings,
) -> Iterator[Harness]:
    _truncate(engine)
    transport = ScriptedTransport()
    app = create_app()

    app.dependency_overrides[documents.provide_session_factory] = lambda: (
        session_factory
    )
    app.dependency_overrides[documents.provide_blob_store] = lambda: blob_store
    app.dependency_overrides[documents.provide_resolver] = lambda: resolver
    app.dependency_overrides[documents.provide_transport] = lambda: transport.transport
    app.dependency_overrides[documents.provide_now] = lambda: clock.now
    app.dependency_overrides[documents.provide_settings] = lambda: settings
    app.dependency_overrides[documents.provide_robots_checker] = lambda: (
        _make_robots_checker(transport, resolver, settings)
    )

    with TestClient(app) as client:
        yield Harness(
            client=client,
            transport=transport,
            clock=clock,
            session_factory=session_factory,
            blob_store=blob_store,
            resolver=resolver,
            settings=settings,
        )
    app.dependency_overrides.clear()
    _truncate(engine)


# --------------------------------------------------------------------------- #
# Shared fixture content
# --------------------------------------------------------------------------- #
def read_html_fixture(name: str) -> bytes:
    """Read a raw HTML fixture (M3 corpus) as bytes."""
    path = Path(__file__).resolve().parents[1] / "fixtures" / "html" / name
    return path.read_bytes()


def read_pdf_fixture(name: str) -> bytes:
    """Read a raw PDF fixture (M4 corpus) as bytes."""
    path = Path(__file__).resolve().parents[1] / "fixtures" / "pdf" / name
    return path.read_bytes()
