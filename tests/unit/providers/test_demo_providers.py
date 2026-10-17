"""PROVIDERS=fake demo mode (C1): a first assemble needs no paid key."""

from __future__ import annotations

import pytest

from app.api.assemble import provide_embeddings, provide_reranker
from app.config import Settings
from app.providers.embeddings.fake import FakeEmbeddingProvider
from app.providers.embeddings.openai import OpenAIEmbeddingProvider
from app.providers.rerank.fake import FakeRerankProvider


def _settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type, call-arg]


def test_fake_providers_in_demo_mode() -> None:
    settings = _settings(providers="fake")
    assert isinstance(provide_embeddings(settings), FakeEmbeddingProvider)
    assert isinstance(provide_reranker(settings), FakeRerankProvider)


def test_real_providers_by_default() -> None:
    assert isinstance(provide_embeddings(_settings()), OpenAIEmbeddingProvider)


def test_demo_mode_is_refused_in_production() -> None:
    with pytest.raises(RuntimeError, match="refused in production"):
        provide_embeddings(_settings(providers="fake", env="production"))


def test_missing_key_is_an_explicit_503_not_a_500() -> None:
    import json

    from app.api.errors import missing_key_handler
    from app.providers.llm.openai import MissingAPIKey

    response = missing_key_handler(
        None,  # type: ignore[arg-type]
        MissingAPIKey("No OpenAI key for embeddings: set EMBEDDINGS_API_KEY."),
    )
    body = json.loads(response.body)
    assert response.status_code == 503
    assert body["error"]["code"] == "provider_not_configured"
    assert "EMBEDDINGS_API_KEY" in body["error"]["message"]
    assert "PROVIDERS=fake" in body["error"]["message"]


def test_app_registers_the_missing_key_handler() -> None:
    from app.main import create_app
    from app.providers.llm.openai import MissingAPIKey

    assert MissingAPIKey in create_app().exception_handlers
