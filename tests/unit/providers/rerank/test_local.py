"""Jina-compatible LOCAL rerank provider, driven by httpx.MockTransport."""

from __future__ import annotations

import json
import math

import httpx
import pytest

from app.providers.local_endpoint import LocalEndpointError
from app.providers.rerank.base import RerankProvider
from app.providers.rerank.local import JinaCompatibleRerankProvider, LocalRerankError

_URL = "http://127.0.0.1:8081"


def _transport(
    scores: list[float], calls: list[dict[str, object]]
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append({"url": str(request.url), "body": body})
        # Results by relevance (vendor order), not input order.
        results = sorted(
            ({"index": i, "relevance_score": s} for i, s in enumerate(scores)),
            key=lambda r: -float(r["relevance_score"]),
        )
        return httpx.Response(200, json={"results": results})

    return httpx.MockTransport(handler)


def test_scores_come_back_in_input_order() -> None:
    calls: list[dict[str, object]] = []
    provider = JinaCompatibleRerankProvider(
        base_url=_URL, model="qwen3-reranker@abc/q8",
        transport=_transport([0.1, 0.9, 0.5], calls),
    )  # fmt: skip
    assert provider.rerank("q", ["a", "b", "c"]) == [0.1, 0.9, 0.5]
    assert calls[0]["url"] == f"{_URL}/v1/rerank"
    assert calls[0]["body"] == {
        "model": "qwen3-reranker@abc/q8",
        "query": "q",
        "documents": ["a", "b", "c"],
        "top_n": 3,
    }


def test_sigmoid_transform_maps_logits() -> None:
    provider = JinaCompatibleRerankProvider(
        base_url=_URL, model="m", score_transform="sigmoid",
        transport=_transport([0.0, 2.0, -800.0], []),
    )  # fmt: skip
    scores = provider.rerank("q", ["a", "b", "c"])
    assert scores[0] == 0.5
    assert scores[1] == round(1 / (1 + math.exp(-2.0)), 6)
    assert scores[2] == 0.0


def test_scores_are_rounded() -> None:
    provider = JinaCompatibleRerankProvider(
        base_url=_URL, model="m", transport=_transport([0.123456789], [])
    )
    assert provider.rerank("q", ["a"]) == [0.123457]


def test_empty_documents_make_no_call() -> None:
    calls: list[dict[str, object]] = []
    provider = JinaCompatibleRerankProvider(
        base_url=_URL, model="m", transport=_transport([], calls)
    )
    assert provider.rerank("q", []) == []
    assert calls == []


def test_missing_index_fails_loudly() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"results": [{"index": 0, "relevance_score": 1}]}
        )

    provider = JinaCompatibleRerankProvider(
        base_url=_URL, model="m", transport=httpx.MockTransport(handler)
    )
    with pytest.raises(LocalRerankError):
        provider.rerank("q", ["a", "b"])


def test_server_error_is_raised() -> None:
    provider = JinaCompatibleRerankProvider(
        base_url=_URL,
        model="m",
        transport=httpx.MockTransport(lambda r: httpx.Response(500)),
    )
    with pytest.raises(httpx.HTTPStatusError):
        provider.rerank("q", ["a"])


def test_refuses_a_hostname() -> None:
    with pytest.raises(LocalEndpointError):
        JinaCompatibleRerankProvider(base_url="http://localhost:8081", model="m")


def test_satisfies_the_protocol() -> None:
    provider = JinaCompatibleRerankProvider(
        base_url=_URL, model="m", transport=_transport([], [])
    )
    assert isinstance(provider, RerankProvider)
