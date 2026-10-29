"""Decoded-size cap, stacked encodings and wall-clock deadline of the client.

Audit 2026-10-28: A1 (a 1.8 KB ``gzip, gzip`` body inflated to 2 GiB before
the 15 MB cap was checked) and M1 (``total_timeout`` was per read, so a server
trickling one byte per second held the request far past it).
"""

from __future__ import annotations

import gzip
import time
import tracemalloc
import zlib
from collections.abc import Iterator

import httpx
import pytest

from app.config import Settings
from app.fetch import client
from app.fetch.errors import FetchFailed, Timeout, TooLarge
from tests.unit.fetch._resolvers import FakeResolver

_RESOLVER = FakeResolver({"public.example.com": ["93.184.216.34"]})
_URL = "http://public.example.com/doc"


def _zeros_gzip(n_bytes: int) -> bytes:
    co = zlib.compressobj(9, zlib.DEFLATED, 31)
    block = b"\0" * (1 << 20)
    parts = [co.compress(block) for _ in range(n_bytes >> 20)]
    parts.append(co.flush())
    return b"".join(parts)


class _Raw(httpx.SyncByteStream):
    """Raw wire bytes in 16 KiB chunks, as a network stream delivers them.

    (``httpx.Response(content=...)`` would decode eagerly in the mock itself.)
    """

    def __init__(self, body: bytes) -> None:
        self._body = body

    def __iter__(self) -> Iterator[bytes]:
        for i in range(0, len(self._body), 16384):
            yield self._body[i : i + 16384]


def _fetch(body: bytes, encoding: str | None, settings: Settings) -> client.FetchResult:
    headers = {"content-type": "text/html"}
    if encoding is not None:
        headers["content-encoding"] = encoding

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=_Raw(body), headers=headers)

    return client.fetch(
        _URL,
        resolver=_RESOLVER,
        transport=httpx.MockTransport(handler),
        settings=settings,
    )


def test_stacked_gzip_bomb_is_refused_without_inflating() -> None:
    inner = _zeros_gzip(256 << 20)  # 256 MiB of zeros
    outer = gzip.compress(inner, 9)
    assert len(outer) < 64 * 1024
    tracemalloc.start()
    try:
        with pytest.raises(FetchFailed):
            _fetch(outer, "gzip, gzip", Settings())
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert peak < 32 * 2**20


@pytest.mark.parametrize("encoding", ["gzip", "deflate"])
def test_single_layer_bomb_stops_at_the_decoded_limit(encoding: str) -> None:
    raw = b"\0" * (64 << 20)
    body = gzip.compress(raw, 9) if encoding == "gzip" else zlib.compress(raw, 9)
    limit = 1 << 20
    tracemalloc.start()
    try:
        with pytest.raises(TooLarge) as excinfo:
            _fetch(body, encoding, Settings(max_body_bytes=limit))
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert excinfo.value.details["limit"] == limit
    # Never more than the limit plus a bounded working set.
    assert peak < 16 * 2**20


def _httpx_decoded(body: bytes, encoding: str) -> bytes:
    response = httpx.Response(200, content=body, headers={"content-encoding": encoding})
    return response.read()


@pytest.mark.parametrize(
    ("encoding", "body"),
    [
        ("gzip", gzip.compress(b"<p>hello gzip</p>" * 5000)),
        ("deflate", zlib.compress(b"<p>hello zlib deflate</p>" * 5000)),
        # Raw deflate (no zlib header): httpx falls back; so must we.
        ("deflate", zlib.compress(b"<p>raw deflate</p>" * 5000, wbits=-15)),
        ("identity", b"<p>plain</p>"),
        ("gzip, identity", gzip.compress(b"<p>with identity</p>")),
        # Encodings httpx does not decode pass through untouched (parity).
        ("br", b"\x8b\x02\x80not-really-brotli"),
    ],
)
def test_normal_bodies_decode_exactly_like_httpx(encoding: str, body: bytes) -> None:
    result = _fetch(body, encoding, Settings())
    assert result.body == _httpx_decoded(body, encoding)


def test_corrupt_gzip_is_a_typed_fetch_error() -> None:
    with pytest.raises(FetchFailed):
        _fetch(b"\x1f\x8b\x08\x00garbage-not-gzip", "gzip", Settings())


class _Trickle(httpx.SyncByteStream):
    """One byte every ``interval`` seconds (slowloris)."""

    def __init__(self, count: int, interval: float) -> None:
        self._count = count
        self._interval = interval

    def __iter__(self) -> Iterator[bytes]:
        for _ in range(self._count):
            time.sleep(self._interval)
            yield b"x"


def test_total_timeout_is_a_wall_clock_deadline() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-type": "text/html"}, stream=_Trickle(40, 0.1)
        )

    started = time.monotonic()
    with pytest.raises(Timeout):
        client.fetch(
            _URL,
            resolver=_RESOLVER,
            transport=httpx.MockTransport(handler),
            settings=Settings(total_timeout=1),
        )
    assert time.monotonic() - started < 2.5
