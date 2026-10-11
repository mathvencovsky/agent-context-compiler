"""Real Anthropic judge provider (SPEC §10.4) — never exercised by tests.

The judge is ``claude-sonnet-5-5`` — a *different* model from the OpenAI
answerer, as SPEC §10.4 requires. The Anthropic SDK is imported lazily inside
:meth:`complete`, so constructing the provider or importing this module needs
neither the SDK configured nor a key. Tests only assert that a missing key fails
loudly; the real API is called solely in the human benchmark run.
"""

from __future__ import annotations

from app.config import Settings, get_settings
from app.index.tokens import count_tokens
from app.providers.llm.base import DEFAULT_SEED, LLMResult, ResponseFormat
from app.providers.llm.openai import MissingAPIKey

#: Cap on judge output; the verdict JSON is tiny.
_MAX_TOKENS = 1024


class AnthropicProvider:
    """Judge backed by the Anthropic API (``claude-sonnet-5-5``)."""

    def __init__(
        self, *, model: str = "claude-sonnet-5-5", settings: Settings | None = None
    ) -> None:
        self.model = model
        self._settings = settings if settings is not None else get_settings()

    def complete(
        self,
        prompt: str,
        *,
        seed: int = DEFAULT_SEED,
        response_format: ResponseFormat = "text",
    ) -> LLMResult:
        """Call the Anthropic messages API.

        Anthropic has no dedicated JSON response mode; when ``response_format``
        is ``"json"`` the harness prompt itself instructs the model to answer
        with a single JSON document (``bench/grade.py``), so the format argument
        is accepted for interface parity.

        Temperature is NOT sent: ``claude-*-5-5`` reports it as deprecated and
        rejects the request. This generation also exposes no ``seed`` parameter,
        so ``seed`` is accepted for interface parity but not forwarded; the fixed
        prompt and the report's bootstrap CIs carry reproducibility
        (DECISIONS.md 2026-10-11).
        """
        api_key = self._settings.bench_judge_api_key
        if not api_key:
            raise MissingAPIKey(
                "BENCH_JUDGE_API_KEY is not set; the Anthropic judge provider "
                "requires it. Set it in .env for the real benchmark run, or use "
                "FakeLLMProvider in tests."
            )

        # Lazy import: keep the SDK off the import path for construction/tests.
        from anthropic import Anthropic
        from anthropic.types import MessageParam

        client = Anthropic(api_key=api_key)
        messages: list[MessageParam] = [{"role": "user", "content": prompt}]
        message = client.messages.create(
            model=self.model,
            max_tokens=_MAX_TOKENS,
            messages=messages,
        )
        text = "".join(block.text for block in message.content if block.type == "text")

        usage = message.usage
        input_tokens = usage.input_tokens if usage else count_tokens(prompt)
        output_tokens = usage.output_tokens if usage else count_tokens(text)

        return LLMResult(
            text=text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model=self.model,
        )
