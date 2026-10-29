"""Scheduling of assemble's concurrent resolves (M10b), without services."""

from __future__ import annotations

import contextvars
import random
import threading
import time
from collections import Counter

import pytest

from app.config import Settings
from app.retrieve.assemble import _in_request_order, _url_keys

_VAR: contextvars.ContextVar[str] = contextvars.ContextVar("_VAR", default="unset")


def _settings(workers: int = 6, per_host: int = 2) -> Settings:
    return Settings(
        assemble_fetch_concurrency=workers, assemble_per_host_concurrency=per_host
    )


def test_same_document_urls_share_a_group() -> None:
    def group(url: str) -> str:
        return _url_keys(url)[1]

    base = group("https://docs.example.com/a?x=1")
    assert group("https://DOCS.Example.com/a?x=1") == base
    assert group("https://docs.example.com./a?x=1") == base
    assert group("https://docs.example.com/a?x=1#frag") == base
    assert group("http://docs.example.com:8080/a?x=1") == base  # coarser: fine
    assert group("https://docs.example.com/b?x=1") != base
    assert group("https://docs.example.com/a?x=2") != base
    assert group("https://bücher.example/a") == group("https://xn--bcher-kva.example/a")
    assert _url_keys("http://[::1/")[1] == "http://[::1/"  # unsplittable


def test_results_come_back_in_request_order() -> None:
    urls = [f"https://h{i % 3}.example/p{i}" for i in range(12)]

    def resolve(url: str) -> str:
        time.sleep(random.uniform(0, 0.02))
        return url.upper()

    got = [f.result() for f in _in_request_order(urls, resolve, _settings())]
    assert got == [u.upper() for u in urls]


def test_limits_per_host_and_overall() -> None:
    urls = [f"https://h{i % 2}.example/p{i}" for i in range(16)]
    lock = threading.Lock()
    open_now: Counter[str] = Counter()
    peak: Counter[str] = Counter()
    total_peak = 0

    def resolve(url: str) -> str:
        nonlocal total_peak
        host = _url_keys(url)[0]
        with lock:
            open_now[host] += 1
            peak[host] = max(peak[host], open_now[host])
            total_peak = max(total_peak, sum(open_now.values()))
        time.sleep(0.02)
        with lock:
            open_now[host] -= 1
        return url

    for future in _in_request_order(urls, resolve, _settings(workers=3, per_host=2)):
        future.result()
    assert max(peak.values()) == 2
    assert total_peak <= 3


def test_same_group_runs_sequentially_in_request_order() -> None:
    urls = [
        "https://h.example/doc",
        "https://h.example/other",
        "https://H.example/doc#x",
        "https://h.example/doc",
    ]
    lock = threading.Lock()
    open_doc = 0
    overlapped = False
    order: list[str] = []

    def tracking(url: str) -> str:
        nonlocal open_doc, overlapped
        same_doc = _url_keys(url)[1] == _url_keys(urls[0])[1]
        with lock:
            order.append(url)
            if same_doc:
                open_doc += 1
                overlapped = overlapped or open_doc > 1
        time.sleep(0.02)
        with lock:
            if same_doc:
                open_doc -= 1
        return url

    for future in _in_request_order(urls, tracking, _settings()):
        future.result()
    doc_calls = [u for u in order if _url_keys(u)[1] == _url_keys(urls[0])[1]]
    assert doc_calls == [urls[0], urls[2], urls[3]]
    assert not overlapped


def test_one_worker_runs_inline_in_the_calling_thread() -> None:
    urls = ["https://a.example/1", "https://b.example/2"]
    threads: list[int] = []

    def resolve(url: str) -> str:
        threads.append(threading.get_ident())
        return url

    for future in _in_request_order(urls, resolve, _settings(workers=1)):
        future.result()
    assert threads == [threading.get_ident()] * 2


def test_context_follows_into_worker_threads() -> None:
    _VAR.set("request-42")
    urls = [f"https://h{i}.example/" for i in range(4)]
    seen = [
        f.result() for f in _in_request_order(urls, lambda _u: _VAR.get(), _settings())
    ]
    assert seen == ["request-42"] * 4


def test_an_exception_reaches_the_consumer_for_its_url_only() -> None:
    urls = ["https://a.example/ok", "https://b.example/boom", "https://c.example/ok"]

    def resolve(url: str) -> str:
        if "boom" in url:
            raise RuntimeError("boom")
        return url

    futures = list(_in_request_order(urls, resolve, _settings()))
    assert futures[0].result() == urls[0]
    with pytest.raises(RuntimeError):
        futures[1].result()
    assert futures[2].result() == urls[2]
