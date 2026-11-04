"""A key in .env alone must never spend (DECISIONS.md 2026-11-04)."""

from __future__ import annotations

import pytest

from app.config import Settings
from app.providers.embeddings.openai import OpenAIEmbeddingProvider
from app.providers.llm.anthropic import AnthropicProvider
from app.providers.llm.openai import OpenAIProvider, PaidCallsDisabled
from app.providers.rerank.jina import JinaRerankProvider

_KEYS = {
    "bench_answerer_api_key": "sk-test",
    "bench_judge_api_key": "sk-test",
    "embeddings_api_key": "sk-test",
    "reranker_api_key": "jina-test",
}


def _settings() -> Settings:
    return Settings(_env_file=None, allow_paid_calls=False, **_KEYS)  # type: ignore[call-arg, arg-type]


def test_paid_calls_are_off_by_default() -> None:
    assert Settings.model_fields["allow_paid_calls"].default is False


def test_embeddings_refuse_before_any_request() -> None:
    with pytest.raises(PaidCallsDisabled, match="ALLOW_PAID_CALLS"):
        OpenAIEmbeddingProvider(settings=_settings()).embed(["text"])


def test_jina_refuses_before_any_request() -> None:
    with pytest.raises(PaidCallsDisabled, match="ALLOW_PAID_CALLS"):
        JinaRerankProvider(settings=_settings()).rerank("q", ["doc"])


def test_openai_llm_refuses_before_any_request() -> None:
    with pytest.raises(PaidCallsDisabled, match="ALLOW_PAID_CALLS"):
        OpenAIProvider(settings=_settings()).complete("prompt")


def test_anthropic_llm_refuses_before_any_request() -> None:
    with pytest.raises(PaidCallsDisabled, match="ALLOW_PAID_CALLS"):
        AnthropicProvider(settings=_settings()).complete("prompt")
