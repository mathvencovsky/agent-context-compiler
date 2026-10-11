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
from app.providers.llm.base import LLMResult, ResponseFormat
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
        temperature: float = 0.0,
        response_format: ResponseFormat = "text",
    ) -> LLMResult:
        """Call the Anthropic messages API.

        Anthropic has no dedicated JSON response mode; when ``response_format``
        is ``"json"`` the harness prompt itself instructs the model to answer
        with a single JSON document (``bench/grade.py``), so the format argument
        is accepted for interface parity.
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
        # This SDK version does not expose a typed ``temperature`` parameter on
        # ``messages.create``; pass it through ``extra_body`` so the judge still
        # runs deterministically at temperature 0 (SPEC §10.3).
        message = client.messages.create(
            model=self.model,
            max_tokens=_MAX_TOKENS,
            messages=messages,
            extra_body={"temperature": temperature},
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
