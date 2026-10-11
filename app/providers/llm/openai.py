"""Real OpenAI answerer provider (SPEC §10.3) — never exercised by tests.

The benchmark answerer is ``gpt-6.1-sol`` (human decision; ``bench/prices.yaml``
``selected:``). The OpenAI SDK is imported lazily inside :meth:`complete` so
merely constructing the provider — or importing this module in a test — needs
neither the SDK configured nor a key. Tests only assert that a missing key fails
loudly; the real API is called solely in the human benchmark run.
"""

from __future__ import annotations

from app.config import Settings, get_settings
from app.index.tokens import count_tokens
from app.providers.llm.base import DEFAULT_SEED, LLMResult, ResponseFormat


class MissingAPIKey(RuntimeError):
    """Raised when a real provider is used without its API key configured."""


class OpenAIProvider:
    """Answerer backed by the OpenAI API (``gpt-6.1-sol``)."""

    def __init__(
        self, *, model: str = "gpt-6.1-sol", settings: Settings | None = None
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
        """Call the OpenAI chat completions API honoring the response format.

        Temperature is NOT sent: the current ``gpt-*`` generation rejects any
        explicit temperature (deprecated). A fixed ``seed`` is passed instead for
        best-effort reproducibility (DECISIONS.md 2026-10-11).
        """
        api_key = self._settings.bench_answerer_api_key
        if not api_key:
            raise MissingAPIKey(
                "BENCH_ANSWERER_API_KEY is not set; the OpenAI answerer provider "
                "requires it. Set it in .env for the real benchmark run, or use "
                "FakeLLMProvider in tests."
            )

        # Lazy import: keep the SDK off the import path for construction/tests.
        from openai import OpenAI
        from openai.types.chat import ChatCompletionUserMessageParam
        from openai.types.shared_params import (
            ResponseFormatJSONObject,
            ResponseFormatText,
        )

        client = OpenAI(api_key=api_key)
        messages: list[ChatCompletionUserMessageParam] = [
            {"role": "user", "content": prompt}
        ]
        fmt: ResponseFormatJSONObject | ResponseFormatText = (
            {"type": "json_object"} if response_format == "json" else {"type": "text"}
        )

        completion = client.chat.completions.create(
            model=self.model,
            seed=seed,
            messages=messages,
            response_format=fmt,
        )
        text = completion.choices[0].message.content or ""

        usage = completion.usage
        input_tokens = usage.prompt_tokens if usage else count_tokens(prompt)
        output_tokens = usage.completion_tokens if usage else count_tokens(text)

        return LLMResult(
            text=text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model=self.model,
        )
