"""Real providers: construction and loud failure when the API key is missing.

These tests never call the vendor APIs. They assert the adapters read the right
Settings field and raise a clear error when the key is absent (the no-network
guard would turn any real call into a failure anyway).
"""

from __future__ import annotations

import pytest

from app.config import Settings
from app.providers.llm.anthropic import AnthropicProvider
from app.providers.llm.base import LLMProvider
from app.providers.llm.openai import MissingAPIKey, OpenAIProvider


def _settings(**overrides: object) -> Settings:
    """Build Settings without reading a .env file, with explicit overrides."""
    return Settings(_env_file=None, **{"allow_paid_calls": True, **overrides})  # type: ignore[call-arg]


def test_providers_satisfy_interface() -> None:
    settings = _settings()
    assert isinstance(OpenAIProvider(settings=settings), LLMProvider)
    assert isinstance(AnthropicProvider(settings=settings), LLMProvider)


def test_default_models_are_distinct() -> None:
    settings = _settings()
    answerer = OpenAIProvider(settings=settings)
    judge = AnthropicProvider(settings=settings)
    assert answerer.model == "gpt-6.1-sol"
    assert judge.model == "claude-sonnet-5-5"
    assert answerer.model != judge.model


def test_openai_missing_key_raises() -> None:
    provider = OpenAIProvider(settings=_settings(bench_answerer_api_key=None))
    with pytest.raises(MissingAPIKey, match="BENCH_ANSWERER_API_KEY"):
        provider.complete("hello")


def test_anthropic_missing_key_raises() -> None:
    provider = AnthropicProvider(settings=_settings(bench_judge_api_key=None))
    with pytest.raises(MissingAPIKey, match="BENCH_JUDGE_API_KEY"):
        provider.complete("hello")


def test_empty_key_raises() -> None:
    provider = OpenAIProvider(settings=_settings(bench_answerer_api_key=""))
    with pytest.raises(MissingAPIKey):
        provider.complete("hello")
