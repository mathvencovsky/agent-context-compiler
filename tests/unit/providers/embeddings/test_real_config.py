"""Real embeddings provider: construction, interface and missing-key failure.

These tests never call the vendor API. They assert the adapter satisfies the
Protocol, resolves its key from the right Settings fields (embeddings key with a
fallback to the answerer key), and fails loudly when neither is set.
"""

from __future__ import annotations

import pytest

from app.config import Settings
from app.providers.embeddings.base import EmbeddingProvider
from app.providers.embeddings.openai import MissingAPIKey, OpenAIEmbeddingProvider


def _settings(**overrides: object) -> Settings:
    """Build Settings without reading a .env file, with explicit overrides."""
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]


def test_satisfies_protocol() -> None:
    provider = OpenAIEmbeddingProvider(settings=_settings())
    assert isinstance(provider, EmbeddingProvider)
    assert provider.model == "text-embedding-3-small"
    assert provider.dim == 1536


def test_missing_both_keys_raises() -> None:
    provider = OpenAIEmbeddingProvider(
        settings=_settings(embeddings_api_key=None, bench_answerer_api_key=None)
    )
    with pytest.raises(MissingAPIKey, match="EMBEDDINGS_API_KEY"):
        provider.embed(["hello"])


def test_empty_keys_raise() -> None:
    provider = OpenAIEmbeddingProvider(
        settings=_settings(embeddings_api_key="", bench_answerer_api_key="")
    )
    with pytest.raises(MissingAPIKey):
        provider.embed(["hello"])


def test_fallback_to_answerer_key_is_read() -> None:
    # Only the answerer key is set: key resolution must succeed (no MissingAPIKey)
    # before the lazy SDK import would run.
    provider = OpenAIEmbeddingProvider(
        settings=_settings(
            embeddings_api_key=None, bench_answerer_api_key="sk-fallback"
        )
    )
    assert provider._api_key() == "sk-fallback"


def test_embeddings_key_takes_precedence() -> None:
    provider = OpenAIEmbeddingProvider(
        settings=_settings(
            embeddings_api_key="sk-embed", bench_answerer_api_key="sk-answerer"
        )
    )
    assert provider._api_key() == "sk-embed"
