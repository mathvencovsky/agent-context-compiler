"""OpenAI-compatible LOCAL embeddings provider, driven by httpx.MockTransport."""

from __future__ import annotations

import json

import httpx
import pytest

from app.providers.embeddings.base import EmbeddingProvider, embed_query
from app.providers.embeddings.fake import FakeEmbeddingProvider
from app.providers.embeddings.local import (
    LocalProviderError,
    OpenAICompatibleEmbeddingProvider,
)
from app.providers.local_endpoint import LocalEndpointError

_URL = "http://127.0.0.1:8080"


def _server(calls: list[dict[str, object]], *, dim: int = 3, shuffle: bool = False):  # type: ignore[no-untyped-def]
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append({"url": str(request.url), "body": body})
        data = [
            {"index": i, "embedding": [float(len(text))] + [0.5] * (dim - 1)}
            for i, text in enumerate(body["input"])
        ]
        if shuffle:
            data.reverse()
        return httpx.Response(200, json={"data": data})

    return httpx.MockTransport(handler)


def _provider(transport: httpx.MockTransport, **kwargs: object):  # type: ignore[no-untyped-def]
    return OpenAICompatibleEmbeddingProvider(
        base_url=_URL + "/", model="qwen3-embed@abc/q8", dim=3,
        transport=transport, **kwargs,  # type: ignore[arg-type]
    )  # fmt: skip


def test_request_shape_and_order() -> None:
    calls: list[dict[str, object]] = []
    provider = _provider(_server(calls, shuffle=True))
    vectors = provider.embed(["a", "bbb"])
    assert vectors == [[1.0, 0.5, 0.5], [3.0, 0.5, 0.5]]
    assert calls[0]["url"] == f"{_URL}/v1/embeddings"
    assert calls[0]["body"] == {"model": "qwen3-embed@abc/q8", "input": ["a", "bbb"]}


def test_batches_long_inputs() -> None:
    calls: list[dict[str, object]] = []
    provider = _provider(_server(calls))
    vectors = provider.embed([str(i) for i in range(70)])
    assert len(vectors) == 70
    assert [len(c["body"]["input"]) for c in calls] == [32, 32, 6]  # type: ignore[index]


def test_empty_input_makes_no_call() -> None:
    calls: list[dict[str, object]] = []
    assert _provider(_server(calls)).embed([]) == []
    assert calls == []


def test_query_prefix_only_on_queries() -> None:
    calls: list[dict[str, object]] = []
    provider = _provider(_server(calls), query_prefix="Instruct: find\nQuery:")
    provider.embed(["doc"])
    provider.embed_query("q")
    assert calls[0]["body"]["input"] == ["doc"]  # type: ignore[index]
    assert calls[1]["body"]["input"] == ["Instruct: find\nQuery:q"]  # type: ignore[index]


def test_wrong_dimension_fails_loudly() -> None:
    provider = _provider(_server([], dim=4))
    with pytest.raises(LocalProviderError, match="EMBEDDING_DIM"):
        provider.embed(["x"])


def test_short_answer_fails_loudly() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": []})

    with pytest.raises(LocalProviderError):
        _provider(httpx.MockTransport(handler)).embed(["x"])


def test_redirect_is_not_followed() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(302, headers={"Location": "http://169.254.169.254/"})

    with pytest.raises(httpx.HTTPStatusError):
        _provider(httpx.MockTransport(handler)).embed(["x"])
    assert seen == [f"{_URL}/v1/embeddings"]


def test_refuses_a_non_loopback_url() -> None:
    with pytest.raises(LocalEndpointError):
        OpenAICompatibleEmbeddingProvider(
            base_url="http://10.0.0.5:8080", model="m", dim=3
        )


def test_satisfies_the_protocol() -> None:
    assert isinstance(_provider(_server([])), EmbeddingProvider)


def test_embed_query_helper_falls_back_to_embed() -> None:
    class Bare:
        model = "bare"
        dim = 2

        def embed(self, texts: list[str]) -> list[list[float]]:
            return [[float(len(t)), 0.0] for t in texts]

    assert embed_query(Bare(), "abc") == [3.0, 0.0]


def test_symmetric_providers_embed_queries_like_documents() -> None:
    fake = FakeEmbeddingProvider(dim=8)
    assert fake.embed_query("hello") == fake.embed(["hello"])[0]
    assert embed_query(fake, "hello") == fake.embed(["hello"])[0]


def test_pad_to_appends_zeros_and_keeps_cosine() -> None:
    calls: list[dict[str, object]] = []
    provider = _provider(_server(calls), pad_to=5)
    (vector,) = provider.embed(["abcd"])
    assert vector == [4.0, 0.5, 0.5, 0.0, 0.0]
    a, b = provider.embed(["ab", "abcdef"])
    plain = _provider(_server(calls))
    pa, pb = plain.embed(["ab", "abcdef"])

    def cos(x: list[float], y: list[float]) -> float:
        dot = sum(i * j for i, j in zip(x, y, strict=True))
        return dot / (sum(i * i for i in x) ** 0.5 * sum(j * j for j in y) ** 0.5)

    assert cos(a, b) == pytest.approx(cos(pa, pb))


def test_pad_to_smaller_than_dim_is_refused() -> None:
    with pytest.raises(ValueError, match="pad_to"):
        _provider(_server([]), pad_to=2)
