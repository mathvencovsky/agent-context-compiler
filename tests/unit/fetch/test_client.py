"""Secure client: redirect re-validation, DNS-rebinding, streaming body cap."""

from __future__ import annotations

import httpx
import pytest

from app.config import Settings
from app.fetch import client
from app.fetch.errors import BlockedDestination, FetchFailed, TooLarge
from tests.unit.fetch._resolvers import FakeResolver, SequenceResolver

SETTINGS = Settings()


def _ok_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, content=b"hello", headers={"content-type": "text/plain"})


def test_basic_fetch_connects_to_validated_ip() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url_host"] = request.url.host
        captured["host_header"] = request.headers.get("host")
        captured["sni"] = request.extensions.get("sni_hostname")
        captured["user_agent"] = request.headers.get("user-agent")
        return httpx.Response(200, content=b"hi", headers={"content-type": "text/html"})

    resolver = FakeResolver({"public.example.com": ["93.184.216.34"]})
    result = client.fetch(
        "http://public.example.com/docs",
        resolver=resolver,
        transport=httpx.MockTransport(handler),
        settings=SETTINGS,
    )

    assert result.status == 200
    assert result.body == b"hi"
    assert result.content_type == "text/html"
    assert result.redirects == 0
    assert captured["url_host"] == "93.184.216.34"
    assert captured["host_header"] == "public.example.com"
    assert captured["sni"] == "public.example.com"
    assert "AgentContextBot/0.1" in str(captured["user_agent"])


def test_redirect_to_private_is_blocked() -> None:
    """A 302 pointing at a host that resolves to a private IP is refused."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers.get("host") == "public.example.com":
            return httpx.Response(
                302, headers={"location": "http://internal.example.com/x"}
            )
        raise AssertionError("second hop should never be sent to the transport")

    resolver = FakeResolver(
        {
            "public.example.com": ["93.184.216.34"],
            "internal.example.com": ["10.0.0.5"],
        }
    )
    with pytest.raises(BlockedDestination):
        client.fetch(
            "http://public.example.com/start",
            resolver=resolver,
            transport=httpx.MockTransport(handler),
            settings=SETTINGS,
        )


def test_too_many_redirects_is_fetch_failed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302, headers={"location": "http://public.example.com/next"}
        )

    resolver = FakeResolver({"public.example.com": ["93.184.216.34"]})
    with pytest.raises(FetchFailed):
        client.fetch(
            "http://public.example.com/start",
            resolver=resolver,
            transport=httpx.MockTransport(handler),
            settings=SETTINGS,
        )


def test_dns_rebinding_uses_validated_ip() -> None:
    """DNS rebinding must not change the destination.

    The resolver returns a PUBLIC IP on the first (validation) call and a
    PRIVATE IP on any later call. The client must connect to the validated
    public IP and must NOT resolve a second time. We prove both:

      * the transport sees the request URL host == the validated public IP
        (never the private rebind target);
      * the Host header and sni_hostname are the original hostname;
      * the resolver was called exactly once.
    """
    public_ip = "93.184.216.34"
    private_ip = "10.0.0.7"
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url_host"] = request.url.host
        captured["host_header"] = request.headers.get("host")
        captured["sni"] = request.extensions.get("sni_hostname")
        return httpx.Response(
            200, content=b"ok", headers={"content-type": "text/plain"}
        )

    resolver = SequenceResolver([[public_ip], [private_ip]])

    result = client.fetch(
        "http://rebind.example.com/data",
        resolver=resolver,
        transport=httpx.MockTransport(handler),
        settings=SETTINGS,
    )

    # The connection targeted the validated public IP, not the private rebind.
    assert captured["url_host"] == public_ip
    assert captured["url_host"] != private_ip
    # Virtual-host and TLS identity preserved as the original name.
    assert captured["host_header"] == "rebind.example.com"
    assert captured["sni"] == "rebind.example.com"
    # Exactly one resolution happened — no re-resolve window for the attacker.
    assert resolver.calls == ["rebind.example.com"]
    assert result.status == 200


def test_body_too_large_streaming_abort() -> None:
    """Body above the limit aborts without buffering the whole body.

    The transport streams 100 chunks of 1 KiB. With a 4 KiB limit, the client
    must raise TooLarge after reading just past the limit, so the number of
    chunks the generator actually produced stays tiny (proving streaming, not
    read-all-then-check).
    """
    chunk = b"x" * 1024
    produced = {"count": 0}

    def gen() -> object:
        for _ in range(100):
            produced["count"] += 1
            yield chunk

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=gen(), headers={"content-type": "text/plain"}
        )

    resolver = FakeResolver({"big.example.com": ["93.184.216.34"]})
    settings = Settings(max_body_bytes=4 * 1024)

    with pytest.raises(TooLarge):
        client.fetch(
            "http://big.example.com/blob",
            resolver=resolver,
            transport=httpx.MockTransport(handler),
            settings=settings,
        )

    # Never consumed the full 100-chunk (100 KiB) stream: at most limit + a few
    # chunks were pulled before aborting.
    assert produced["count"] <= 4 + 2


def test_content_length_over_limit_short_circuits() -> None:
    """A declared Content-Length over the limit aborts before reading the body."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"small body but lying header",
            headers={"content-type": "text/plain", "content-length": "999999999"},
        )

    resolver = FakeResolver({"liar.example.com": ["93.184.216.34"]})
    settings = Settings(max_body_bytes=1024)
    with pytest.raises(TooLarge):
        client.fetch(
            "http://liar.example.com/x",
            resolver=resolver,
            transport=httpx.MockTransport(handler),
            settings=settings,
        )
