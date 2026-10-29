"""The local-provider endpoint exception: loopback IP literals only."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.api.assemble import provide_embeddings, provide_reranker
from app.config import Settings
from app.providers.embeddings.local import OpenAICompatibleEmbeddingProvider
from app.providers.embeddings.openai import OpenAIEmbeddingProvider
from app.providers.local_endpoint import (
    LocalEndpointError,
    local_client,
    validate_local_base_url,
)
from app.providers.rerank.jina import JinaRerankProvider
from app.providers.rerank.local import JinaCompatibleRerankProvider


def _settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type, call-arg]


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://127.0.0.1:8080", "http://127.0.0.1:8080"),
        ("http://127.0.0.1:8080/", "http://127.0.0.1:8080"),
        ("https://127.0.0.2:9000/base/", "https://127.0.0.2:9000/base"),
        ("http://[::1]:8080", "http://[::1]:8080"),
    ],
)
def test_accepts_loopback_literals(url: str, expected: str) -> None:
    assert validate_local_base_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8080",  # host name: DNS could answer anything
        "http://10.0.0.1:8080",
        "http://169.254.169.254/",
        "http://[::ffff:10.0.0.1]:80",
        "http://0.0.0.0:8080",
        "ftp://127.0.0.1/",
        "file:///etc/passwd",
        "http://user:pw@127.0.0.1:8080",
        "http://127.0.0.1:8080/?x=1",
        "http://127.0.0.1:8080/#f",
        "http://127.0.0.1:99999",
        "http://api.jina.ai",
    ],
)
def test_refuses_everything_else(url: str) -> None:
    with pytest.raises(LocalEndpointError):
        validate_local_base_url(url)


def test_settings_validate_the_urls() -> None:
    with pytest.raises(ValidationError):
        _settings(reranker_base_url="http://example.com:8081")
    with pytest.raises(ValidationError):
        _settings(embeddings_base_url="http://192.168.0.2:8080")
    assert _settings(reranker_base_url="").reranker_base_url is None
    assert (
        _settings(reranker_base_url="http://127.0.0.1:8081/").reranker_base_url
        == "http://127.0.0.1:8081"
    )


def test_client_never_follows_redirects_or_proxies() -> None:
    with local_client() as client:
        assert client.follow_redirects is False
        assert client.trust_env is False


def test_defaults_keep_the_vendor_providers() -> None:
    settings = _settings()
    assert isinstance(provide_embeddings(settings), OpenAIEmbeddingProvider)
    assert isinstance(provide_reranker(settings), JinaRerankProvider)


def test_base_urls_select_the_local_providers() -> None:
    settings = _settings(
        reranker_base_url="http://127.0.0.1:8081",
        reranker_model="Qwen3-Reranker-0.6B-Q8_0",
        reranker_score_transform="sigmoid",
        embeddings_base_url="http://127.0.0.1:8080",
        embedding_model="local-embed",
        embeddings_query_prefix="Q:",
    )
    reranker = provide_reranker(settings)
    assert isinstance(reranker, JinaCompatibleRerankProvider)
    assert reranker.model == "Qwen3-Reranker-0.6B-Q8_0"
    assert reranker.score_transform == "sigmoid"
    embeddings = provide_embeddings(settings)
    assert isinstance(embeddings, OpenAICompatibleEmbeddingProvider)
    assert (embeddings.model, embeddings.dim, embeddings.query_prefix) == (
        "local-embed",
        1536,
        "Q:",
    )


def test_local_embeddings_refuse_a_dimension_the_column_cannot_hold() -> None:
    settings = _settings(
        embeddings_base_url="http://127.0.0.1:8080", embedding_dim=1024
    )
    with pytest.raises(RuntimeError, match="vector\\(1536\\)"):
        provide_embeddings(settings)


def test_pad_to_column_accepts_a_smaller_local_model() -> None:
    settings = _settings(
        embeddings_base_url="http://127.0.0.1:8080",
        embedding_dim=1024,
        embeddings_pad_to_column=True,
    )
    embeddings = provide_embeddings(settings)
    assert isinstance(embeddings, OpenAICompatibleEmbeddingProvider)
    assert (embeddings.dim, embeddings.pad_to) == (1024, 1536)


def test_pad_to_column_still_refuses_a_larger_model() -> None:
    settings = _settings(
        embeddings_base_url="http://127.0.0.1:8080",
        embedding_dim=2048,
        embeddings_pad_to_column=True,
    )
    with pytest.raises(RuntimeError, match="vector\\(1536\\)"):
        provide_embeddings(settings)


def test_demo_mode_wins_over_local_urls() -> None:
    from app.providers.embeddings.fake import FakeEmbeddingProvider
    from app.providers.rerank.fake import FakeRerankProvider

    settings = _settings(
        providers="fake",
        reranker_base_url="http://127.0.0.1:8081",
        embeddings_base_url="http://127.0.0.1:8080",
    )
    assert isinstance(provide_embeddings(settings), FakeEmbeddingProvider)
    assert isinstance(provide_reranker(settings), FakeRerankProvider)
