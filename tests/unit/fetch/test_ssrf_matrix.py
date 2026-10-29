"""SSRF matrix cases not proven elsewhere (docs/security/SSRF_MATRIX.md).

Each test drives the real guarded client (``app.fetch.client.fetch``) with a
recording ``httpx.MockTransport`` and asserts the forbidden destination
received NOTHING: the recorder stays empty, or holds only the allowed hops.
No socket is ever opened (the transport is mocked, the resolver is a fake).
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from app.config import Settings
from app.fetch import client, render
from app.fetch.errors import (
    BlockedDestination,
    FetchError,
    FetchFailed,
    InvalidURL,
    UrlRejectedSecret,
)
from app.fetch.guard import validate_url
from app.fetch.url_secrets import reject_secret_url
from tests.unit.fetch._resolvers import FakeResolver

SETTINGS = Settings(_env_file=None)  # type: ignore[call-arg]
PUBLIC_IP = "93.184.216.34"
RESOLVER = FakeResolver(
    {
        "public.example.com": [PUBLIC_IP],
        "other.example.com": ["93.184.216.35"],
        "metadata.example.com": ["169.254.169.254"],
        "aws6-metadata.example.com": ["fd00:ec2::254"],
        "private172.example.com": ["172.16.5.4"],
        "private192.example.com": ["192.168.1.10"],
        "ula.example.com": ["fd12:3456::1"],
        "linklocal6.example.com": ["fe80::1"],
        "loopback6.example.com": ["::1"],
    }
)


class Recorder:
    """A MockTransport that records every request it is asked to send."""

    def __init__(self, reply: Callable[[httpx.Request], httpx.Response]) -> None:
        self.requests: list[httpx.Request] = []
        self._reply = reply

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._reply(request)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)


def _ok(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, content=b"ok", headers={"content-type": "text/plain"})


def _fetch(url: str, recorder: Recorder) -> client.FetchResult:
    return client.fetch(
        url, resolver=RESOLVER, transport=recorder.transport, settings=SETTINGS
    )


# --------------------------------------------------------------------------- #
# Destinations: every forbidden address class, literal and by name
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "url",
    [
        # Loopback IPv4 (beyond 127.0.0.1) and the unspecified address.
        "http://127.0.0.2/",
        "http://0.0.0.0/",
        # Private IPv4, literal and resolved.
        "http://10.1.2.3/",
        "http://172.16.0.1/",
        "http://192.168.0.1/",
        "http://private172.example.com/",
        "http://private192.example.com/",
        # Link-local / cloud metadata, literal, IPv4-mapped and by name.
        "http://169.254.169.254/latest/meta-data/",
        "http://[::ffff:a9fe:a9fe]/",
        "http://metadata.example.com/computeMetadata/v1/",
        "http://[fd00:ec2::254]/latest/meta-data/",
        "http://aws6-metadata.example.com/",
        # IPv6 loopback, ULA and link-local, literal and resolved.
        "http://[::1]/",
        "http://loopback6.example.com/",
        "http://[fc00::1]/",
        "http://[fd12:3456::1]/",
        "http://ula.example.com/",
        "http://[fe80::1]/",
        "http://linklocal6.example.com/",
        # Unicode digit and ideographic-dot forms of 127.0.0.1 (IDNA-folded).
        "http://①②⑦.0.0.1/",
        "http://127。0。0。1/",
    ],
)
def test_forbidden_destination_receives_nothing(url: str) -> None:
    recorder = Recorder(_ok)
    with pytest.raises(BlockedDestination):
        _fetch(url, recorder)
    assert recorder.requests == []


@pytest.mark.parametrize(
    "url",
    [
        "http://0177.0.0.1/",  # octal dotted
        "http://0x7f.0.0.1/",  # hex dotted
        "http://0x7f.1/",  # hex short form
        "http://127.000.000.001/",  # zero-padded
    ],
)
def test_alternative_ip_notation_is_refused_before_resolving(url: str) -> None:
    resolver = FakeResolver({})
    recorder = Recorder(_ok)
    with pytest.raises(InvalidURL):
        client.fetch(
            url, resolver=resolver, transport=recorder.transport, settings=SETTINGS
        )
    assert resolver.calls == []
    assert recorder.requests == []


@pytest.mark.parametrize(
    "url",
    [
        "ftp://public.example.com/file",
        "file:///etc/passwd",
        "gopher://public.example.com:70/_SET",
        "dict://public.example.com:11211/stat",
        "ws://public.example.com/socket",
        "data:text/html,<script>1</script>",
        "javascript:alert(1)",
    ],
)
def test_non_http_schemes_are_refused_before_resolving(url: str) -> None:
    resolver = FakeResolver({})
    recorder = Recorder(_ok)
    with pytest.raises(InvalidURL):
        client.fetch(
            url, resolver=resolver, transport=recorder.transport, settings=SETTINGS
        )
    assert resolver.calls == []
    assert recorder.requests == []


# --------------------------------------------------------------------------- #
# URL userinfo
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "url",
    [
        # Classic confusion: the real host is 127.0.0.1, not public.example.com.
        "http://public.example.com@127.0.0.1/",
        "http://public.example.com:80@169.254.169.254/",
        "http://user:secret@public.example.com/",
    ],
)
def test_userinfo_urls_are_refused_before_any_request(url: str) -> None:
    """``resolve``/``assemble``/the alternate fetcher call ``reject_secret_url``
    before the guard; the guard itself judges the REAL host after ``@``."""
    with pytest.raises(UrlRejectedSecret):
        reject_secret_url(url)


def test_guard_judges_the_host_after_the_at_sign() -> None:
    recorder = Recorder(_ok)
    with pytest.raises(BlockedDestination):
        _fetch("http://public.example.com@127.0.0.1/", recorder)
    assert recorder.requests == []


# --------------------------------------------------------------------------- #
# Redirects: chains, loops
# --------------------------------------------------------------------------- #
def test_redirect_chain_reaching_metadata_stops_before_that_hop() -> None:
    """public -> other (public) -> metadata: the third hop is never sent."""

    def reply(request: httpx.Request) -> httpx.Response:
        host = request.headers["host"]
        if host == "public.example.com":
            return httpx.Response(
                302, headers={"location": "http://other.example.com/2"}
            )
        if host == "other.example.com":
            return httpx.Response(
                301, headers={"location": "http://169.254.169.254/latest/meta-data/"}
            )
        raise AssertionError(f"forbidden hop sent to {host}")

    recorder = Recorder(reply)
    with pytest.raises(BlockedDestination):
        _fetch("http://public.example.com/1", recorder)
    assert [r.headers["host"] for r in recorder.requests] == [
        "public.example.com",
        "other.example.com",
    ]
    assert all(r.url.host in (PUBLIC_IP, "93.184.216.35") for r in recorder.requests)


def test_redirect_loop_is_capped_with_a_typed_error() -> None:
    """A -> B -> A -> ... stops after max_redirects hops with ``fetch_failed``."""

    def reply(request: httpx.Request) -> httpx.Response:
        nxt = "/b" if request.url.path == "/a" else "/a"
        return httpx.Response(302, headers={"location": nxt})

    recorder = Recorder(reply)
    with pytest.raises(FetchFailed) as info:
        _fetch("http://public.example.com/a", recorder)
    assert info.value.code == "fetch_failed"
    assert info.value.details["redirects"] == SETTINGS.max_redirects
    assert len(recorder.requests) == SETTINGS.max_redirects + 1


def test_redirect_to_a_non_http_scheme_is_refused() -> None:
    def reply(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "file:///etc/passwd"})

    recorder = Recorder(reply)
    with pytest.raises(InvalidURL):
        _fetch("http://public.example.com/a", recorder)
    assert len(recorder.requests) == 1


# --------------------------------------------------------------------------- #
# Retry and fallback
# --------------------------------------------------------------------------- #
def test_a_failed_request_is_never_retried() -> None:
    """The client has no retry loop: one failure, one request, typed error."""

    def reply(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    recorder = Recorder(reply)
    with pytest.raises(FetchError):
        _fetch("http://public.example.com/a", recorder)
    assert len(recorder.requests) == 1


def test_browser_fallback_revalidates_before_launching(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The render fallback guards its entry URL before any browser exists."""

    def no_browser() -> None:
        raise AssertionError("the browser must not be launched")

    monkeypatch.setattr(render, "sync_playwright", no_browser)
    for url in ("http://10.0.0.1/", "http://metadata.example.com/"):
        with pytest.raises(BlockedDestination):
            render.render_url(url, resolver=RESOLVER, settings=SETTINGS)


def test_guard_result_pins_the_validated_address() -> None:
    """What the fallback and the client connect to is the validated IP."""
    result = validate_url(
        "http://public.example.com/x", resolver=RESOLVER, settings=SETTINGS
    )
    assert result.normalized_url == "http://public.example.com/x"
    recorder = Recorder(_ok)
    _fetch("http://public.example.com/x", recorder)
    assert [r.url.host for r in recorder.requests] == [PUBLIC_IP]
