"""LLM providers for the M2 benchmark harness.

The benchmark answers questions with one LLM and judges them with a different
one (SPEC §10.3/§10.4). Both sit behind :class:`~app.providers.llm.base.LLMProvider`
so the harness and every test depend on the interface, not on a vendor SDK. The
deterministic :class:`~app.providers.llm.fake.FakeLLMProvider` backs all tests
(zero network); the real OpenAI/Anthropic adapters are only used in the human
benchmark run.
"""
