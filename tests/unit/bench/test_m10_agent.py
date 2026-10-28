"""Offline tests for the M10 agent runner (PREREG §4, §6, §10).

The Agent SDK is replaced by a fake query function yielding scripted SDK
messages and the judges by scripted providers: no CLI, no network, no paid call.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from app.providers.llm.base import LLMResult
from bench import m10_agent
from bench.corpus_store import CorpusStore
from bench.m10_agent import (
    ARM_TOOLS,
    CONTEXT7_URL,
    DOCS_SYSTEM,
    MODEL,
    NO_DOCS_SYSTEM,
    PREREG_ARMS,
    WEBFETCH_MODEL,
    HarnessError,
    RunContext,
    arm_order,
    check_init,
    run,
    run_session,
    session_options,
    task_prompt,
    tool_names,
    usage_by_model,
)
from bench.m10_tools import INFRA_ERROR
from bench.tasks_schema import ExternalTask, Source

_PREREG = Path(__file__).resolve().parents[3] / "bench" / "PREREG.md"
_URL = "https://docs.python.org/3/library/functions.html"
_PRICES = {
    "models": {
        MODEL: {
            "input_usd_per_1m": 2.0,
            "cache_write_usd_per_1m": 2.5,
            "cache_read_usd_per_1m": 0.2,
            "output_usd_per_1m": 10.0,
        },
        WEBFETCH_MODEL: {
            "input_usd_per_1m": 0.1,
            "cache_write_usd_per_1m": 0.125,
            "cache_read_usd_per_1m": 0.01,
            "output_usd_per_1m": 0.5,
        },
    }
}
#: The arms without an MCP server.
_NO_SERVER = ("no_docs", "webfetch_live")
_FINAL = 'abs() it is.\n{"answer": "Use abs(x).", "citations": []}'


def _task(**extra: object) -> ExternalTask:
    fields = {
        "id": "so-1",
        "split": "pilot",
        "site": "python",
        "task_type": "fact",
        "urls": [_URL, "https://docs.python.org/3/library/math.html"],
        "cited_urls": [_URL],
        "question_title": "Absolute value?",
        "question": "How do I get |x| in Python?",
        "reference_answer": "Use abs(x).",
        "expected_evidence": [],
        "source": Source(
            question_url="https://stackoverflow.com/q/1",
            answer_url="https://stackoverflow.com/a/2",
            question_author="Ada",
            answer_author="Linus",
            license="CC BY-SA 4.0",
            created="2023-01-02",
        ),
    }
    return ExternalTask(**(fields | extra))


class ScriptedJudge:
    """Returns the scripted replies in order (the last one repeats)."""

    model = "scripted-judge"

    def __init__(self, *replies: str) -> None:
        self.replies = list(replies)
        self.calls = 0

    def complete(self, prompt: str, **_kwargs: object) -> LLMResult:
        reply = self.replies[min(self.calls, len(self.replies) - 1)]
        self.calls += 1
        return LLMResult(text=reply, input_tokens=1, output_tokens=1, model=self.model)


_CORRECT = '{"correct": true, "reason": "same fix"}'


def _init(arm: str, **extra: object) -> SystemMessage:
    data = {
        "tools": tool_names(arm),
        "mcp_servers": []
        if arm in _NO_SERVER
        else [{"name": "docs", "status": "connected"}],
        "model": MODEL,
        "permissionMode": "dontAsk",
        "apiKeySource": "ANTHROPIC_API_KEY",
        **extra,
    }
    return SystemMessage(subtype="init", data=data)


def _result(**extra: Any) -> ResultMessage:
    fields: dict[str, Any] = {
        "subtype": "success",
        "duration_ms": 10,
        "duration_api_ms": 8,
        "is_error": False,
        "num_turns": 2,
        "session_id": "s",
        "result": _FINAL,
        "model_usage": {MODEL: _AGENT_USAGE},
    }
    return ResultMessage(**(fields | extra))


_AGENT_USAGE = {
    "inputTokens": 1_000,
    "outputTokens": 200,
    "cacheReadInputTokens": 5_000,
    "cacheCreationInputTokens": 3_000,
    "webSearchRequests": 0,
    "costUSD": 0.0,
    "contextWindow": 1_000_000,
    "maxOutputTokens": 64_000,
}
_AGENT_USD = (1_000 * 2.0 + 3_000 * 2.5 + 5_000 * 0.2 + 200 * 10.0) / 1e6


def _session(
    arm: str = "full_page",
    *,
    tool_text: str = "abs(x): absolute value",
    tool_error: bool = False,
    result: ResultMessage | None = None,
) -> list[object]:
    return [
        _init(arm),
        AssistantMessage(
            content=[
                ToolUseBlock(id="t1", name=tool_names(arm)[0], input={"url": _URL})
            ],
            model=MODEL,
        ),
        UserMessage(
            content=[
                ToolResultBlock(
                    tool_use_id="t1",
                    content=[{"type": "text", "text": tool_text}],
                    is_error=tool_error,
                )
            ]
        ),
        AssistantMessage(content=[TextBlock(text=_FINAL)], model=MODEL),
        result or _result(),
    ]


class FakeQuery:
    """Stands in for ``claude_agent_sdk.query``: one scripted run per call."""

    def __init__(self, *runs: list[object] | Exception) -> None:
        self.runs = list(runs)
        self.options: list[Any] = []

    async def __call__(self, *, prompt: str, options: Any) -> AsyncIterator[object]:
        self.options.append(options)
        run = self.runs[min(len(self.options), len(self.runs)) - 1]
        if isinstance(run, Exception):
            raise run
        for message in run:
            yield message


def _ctx(
    tmp_path: Path, store: CorpusStore, query: FakeQuery, *judges: ScriptedJudge
) -> RunContext:
    primary = judges[0] if judges else ScriptedJudge(_CORRECT)
    return RunContext(
        out=tmp_path / "m10",
        data_root=tmp_path,
        api_key="test-key",
        prices=_PRICES,
        primary=primary,
        second=judges[1] if len(judges) > 1 else None,
        store=store,
        commit="abc123",
        query_fn=query,
    )


def _lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _prereg_block(title: str) -> str:
    text = _PREREG.read_text(encoding="utf-8")
    block = re.search(re.escape(title) + r"\s*```\n(.*?)\n```", text, re.DOTALL)
    assert block is not None
    return block.group(1)


def test_prompts_are_the_prereg_appendix_b_text_verbatim() -> None:
    assert _prereg_block("System prompt, documentation arms:") == DOCS_SYSTEM
    assert _prereg_block("System prompt, `no_docs`:") == NO_DOCS_SYSTEM
    template = _prereg_block("Task prompt (all arms):")
    task = _task()
    expected = (
        template.replace("<question title>", task.question_title)
        .replace("<question body as plain text>", task.question)
        .replace("<one URL per line>", "\n".join(task.urls))
    )
    assert task_prompt(task) == expected


@pytest.mark.parametrize("arm", list(ARM_TOOLS))
def test_options_meet_prereg_section_4(arm: str, tmp_path: Path) -> None:
    server = m10_agent.tool_server(arm, tmp_path, tmp_path / "log.jsonl")
    options = session_options(
        arm,
        workdir=tmp_path / "w",
        config_dir=tmp_path / "c",
        api_key="k",
        server=server,
    )
    assert options.model == "claude-sonnet-5-5"
    assert options.max_turns == 15
    assert options.system_prompt == (
        NO_DOCS_SYSTEM if arm == "no_docs" else DOCS_SYSTEM
    )
    # no built-in tool, except WebFetch in its own arm
    assert options.tools == (["WebFetch"] if arm == "webfetch_live" else [])
    if arm != "webfetch_live":
        assert options.allowed_tools == tool_names(arm)
    assert options.permission_mode == "dontAsk"  # never a bypass
    assert options.setting_sources == []
    assert options.strict_mcp_config and options.verbatim_prompts
    assert not options.continue_conversation and options.resume is None
    assert options.cwd == str(tmp_path / "w")
    assert options.env == {
        "ANTHROPIC_API_KEY": "k",
        "CLAUDE_CONFIG_DIR": str(tmp_path / "c"),
        "MAX_MCP_OUTPUT_TOKENS": "1000000",
        "ENABLE_TOOL_SEARCH": "false",  # Amendment 11: no deferred tool definitions
        # Amendment 12: today's defaults, pinned against product changes
        "CLAUDE_CODE_EFFORT_LEVEL": "medium",
        "FORCE_PROMPT_CACHING_5M": "1",
        "CLAUDE_CODE_DISABLE_REFUSAL_FALLBACK": "1",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",  # Amendment 14
    }
    if arm in _NO_SERVER:
        assert options.mcp_servers == {}
    elif arm == "context7_live":
        assert options.mcp_servers["docs"]["url"] == CONTEXT7_URL  # type: ignore[index]
    else:
        assert options.mcp_servers["docs"]["args"][:3] == ["-m", "bench.m10_tools", arm]  # type: ignore[index]


def test_webfetch_may_fetch_the_documentation_hosts_only(tmp_path: Path) -> None:
    options = session_options(
        "webfetch_live", workdir=tmp_path, config_dir=tmp_path, api_key="k", server=None
    )
    rules = options.allowed_tools
    assert "WebFetch(domain:docs.python.org)" in rules
    assert {"WebFetch(domain:reactjs.org)", "WebFetch(domain:react.dev)"} <= set(rules)
    # a bare rule would allow every site, the reference answer's included
    assert "WebFetch" not in rules
    assert not any("stackoverflow" in rule for rule in rules)


def test_paid_retrieval_arms_log_their_provider_calls(tmp_path: Path) -> None:
    """Front F L3: nina and strong_rag log embedding and rerank calls."""
    usage = tmp_path / "usage.jsonl"
    for arm in ("nina", "strong_rag"):
        server = m10_agent.tool_server(arm, tmp_path, tmp_path / "log", usage=usage)
        args = server["args"]  # type: ignore[index]
        assert args[args.index("--usage") + 1] == str(usage)
    server = m10_agent.tool_server("full_page", tmp_path, tmp_path / "l", usage=usage)
    assert "--usage" not in server["args"]  # type: ignore[index]
    # nina keeps its candidate log; strong_rag never had one
    nina = m10_agent.tool_server("nina", tmp_path, tmp_path / "log", usage=usage)
    assert "--log" in nina["args"]  # type: ignore[index]


def test_a_session_writes_its_provider_usage_beside_the_candidates(
    tmp_path: Path, store: CorpusStore
) -> None:
    query = FakeQuery(_session("strong_rag"))
    run_session(_task(), "strong_rag", _ctx(tmp_path, store, query))
    server = query.options[0].mcp_servers["docs"]  # type: ignore[index]
    args = server["args"]
    usage = Path(args[args.index("--usage") + 1])
    assert usage.parent == tmp_path / "m10" / "provider_usage"
    assert usage.name.endswith("__strong_rag__0.jsonl")


def test_context7_is_the_hosted_server_with_the_key() -> None:
    server = m10_agent.tool_server("context7_live", Path("d"), Path("l"), "ctx7sk-1")
    assert server == {
        "type": "http",
        "url": "https://mcp.context7.com/mcp",
        "headers": {"Authorization": "Bearer ctx7sk-1"},
    }


@pytest.mark.parametrize("arm", list(ARM_TOOLS))
def test_init_check_passes_for_every_arm(arm: str) -> None:
    check_init(_init(arm).data, arm)  # as the real CLI 2.1.301 reports it


def test_clean_environ_drops_the_runners_own_claude_variables() -> None:
    environ = {
        "CLAUDE_EFFORT": "max",
        "CLAUDE_CODE_EMIT_TOOL_USE_SUMMARIES": "1",
        "ANTHROPIC_BASE_URL": "https://proxy.example",
        "CLAUDE_CODE_GIT_BASH_PATH": r"C:\Git\bin\bash.exe",
        "PATH": r"C:\Windows",
        "M10_AGENT_API_KEY": "k",
    }

    m10_agent.clean_environ(environ)

    assert environ == {
        "CLAUDE_CODE_GIT_BASH_PATH": r"C:\Git\bin\bash.exe",
        "PATH": r"C:\Windows",
        "M10_AGENT_API_KEY": "k",
    }


def test_arm_order_is_a_permutation_seeded_by_the_task_id() -> None:
    assert sorted(arm_order("so-1")) == sorted(PREREG_ARMS)
    assert arm_order("so-1") == arm_order("so-1")
    assert len({tuple(arm_order(f"so-{i}")) for i in range(20)}) > 1


def test_session_records_usage_cost_effort_and_verdict(
    tmp_path: Path, store: CorpusStore
) -> None:
    query = FakeQuery(_session())
    second = ScriptedJudge('{"correct": false, "reason": "no"}')
    ctx = _ctx(tmp_path, store, query, ScriptedJudge(_CORRECT), second)

    record = run_session(_task(), "full_page", ctx)

    assert record.correct is True and record.correct_judge2 is False
    assert (record.input_tokens, record.output_tokens) == (1_000, 200)
    assert record.cache_read_input_tokens == 5_000
    assert record.cache_creation_input_tokens == 3_000
    assert record.cost_usd == pytest.approx(_AGENT_USD)
    assert (record.turns, record.tool_calls) == (2, 1)
    assert record.tool_result_tokens > 0
    assert not record.hit_max_turns and not record.infra_error
    assert record.git_commit == "abc123"
    assert record.citation_correct is None  # no gold evidence
    transcript = _lines(ctx.out / "transcripts" / "so-1__full_page__0.jsonl")
    assert [m["type"] for m in transcript][0] == "SystemMessage"
    assert transcript[-1]["type"] == "ResultMessage"
    spend = _lines(ctx.out / "spend.jsonl")
    assert spend[0]["cost_usd"] == pytest.approx(_AGENT_USD)
    assert spend[0]["usage"][MODEL]["output_tokens"] == 200
    roles = [j["role"] for j in _lines(ctx.out / "judgments.jsonl")]
    assert roles[:2] == ["primary", "second"]
    assert not (ctx.out / "answers.jsonl").exists()  # the pilot is not labelled


def test_infrastructure_failure_is_retried(tmp_path: Path, store: CorpusStore) -> None:
    query = FakeQuery(RuntimeError("CLI crashed"), _session())
    ctx = _ctx(tmp_path, store, query)

    record = run_session(_task(), "full_page", ctx)

    assert not record.infra_error and record.correct is True
    assert len(query.options) == 2
    spend = _lines(ctx.out / "spend.jsonl")
    assert [s["session"] for s in spend] == ["so-1__full_page__0", "so-1__full_page__1"]
    assert "CLI crashed" in spend[0]["infra_error"]
    # a fresh empty directory and config dir per attempt
    assert query.options[0].cwd != query.options[1].cwd


def test_session_failing_every_attempt_is_excluded(
    tmp_path: Path, store: CorpusStore
) -> None:
    judge = ScriptedJudge(_CORRECT)
    query = FakeQuery(RuntimeError("API 529"))
    ctx = _ctx(tmp_path, store, query, judge)

    record = run_session(_task(), "nina", ctx)

    assert record.infra_error and record.correct is None
    assert len(query.options) == 3  # 1 + 2 retries
    assert judge.calls == 0


@pytest.mark.parametrize(
    "init",
    [
        {"tools": ["Bash", "mcp__docs__fetch_page"]},
        {"apiKeySource": "none"},  # a subscription login
        {"model": "claude-opus-5-5"},
        {"permissionMode": "bypassPermissions"},
    ],
)
def test_a_session_breaking_section_4_stops_the_run(
    tmp_path: Path, store: CorpusStore, init: dict[str, object]
) -> None:
    query = FakeQuery([_init("full_page", **init), _result()])
    with pytest.raises(HarnessError):
        run_session(_task(), "full_page", _ctx(tmp_path, store, query))


def test_a_tool_server_down_is_infrastructure(
    tmp_path: Path, store: CorpusStore
) -> None:
    down = _init("nina", mcp_servers=[{"name": "docs", "status": "failed"}])
    query = FakeQuery([down, _result()])

    record = run_session(_task(), "nina", _ctx(tmp_path, store, query))

    assert record.infra_error and len(query.options) == 3


def test_an_internal_tool_error_is_infrastructure(
    tmp_path: Path, store: CorpusStore
) -> None:
    failing = _session(
        "strong_rag", tool_text=f"{INFRA_ERROR}: HTTPStatusError", tool_error=True
    )
    query = FakeQuery(failing, _session("strong_rag"))

    record = run_session(_task(), "strong_rag", _ctx(tmp_path, store, query))

    assert not record.infra_error and len(query.options) == 2


def test_an_agent_side_tool_error_is_not_infrastructure(
    tmp_path: Path, store: CorpusStore
) -> None:
    query = FakeQuery(_session(tool_text="page not found: x", tool_error=True))

    record = run_session(_task(), "full_page", _ctx(tmp_path, store, query))

    assert not record.infra_error and len(query.options) == 1


@pytest.mark.parametrize(
    ("arm", "text"),
    [
        ("webfetch_live", "Unable to verify if domain react.dev is safe to fetch."),
        ("webfetch_live", "The safety check for domain react.dev is rate-limited"),
        ("context7_live", "Request failed with status 503. Please try again later."),
        ("context7_live", "Error fetching library context. Please try again later."),
    ],
)
def test_a_live_service_failure_is_infrastructure(
    arm: str, text: str, tmp_path: Path, store: CorpusStore
) -> None:
    query = FakeQuery(_session(arm, tool_text=text, tool_error=True), _session(arm))

    record = run_session(_task(), arm, _ctx(tmp_path, store, query))

    assert not record.infra_error and len(query.options) == 2


@pytest.mark.parametrize(
    ("arm", "text"),
    [
        ("webfetch_live", "Request failed with status code 404"),
        (
            "context7_live",
            "The library you are trying to access does not exist. Please try with a "
            "different library ID.",
        ),
    ],
)
def test_a_missing_live_page_is_the_agents_failure(
    arm: str, text: str, tmp_path: Path, store: CorpusStore
) -> None:
    query = FakeQuery(_session(arm, tool_text=text, tool_error=True))

    record = run_session(_task(), arm, _ctx(tmp_path, store, query))

    assert not record.infra_error and len(query.options) == 1


def test_context7_refusing_the_quota_stops_the_run_with_its_spend_kept(
    tmp_path: Path, store: CorpusStore
) -> None:
    # resolve-library-id sends Context7's refusal as plain text, not as an error
    refused = _session(
        "context7_live",
        tool_text="Rate limited or quota exceeded. Upgrade your plan at "
        "https://context7.com/plans for higher limits.",
    )
    ctx = _ctx(tmp_path, store, FakeQuery(refused))

    with pytest.raises(HarnessError, match="Context7"):
        run([_task()], ["context7_live"], ctx, cap_usd=100.0)

    (spend,) = _lines(ctx.out / "spend.jsonl")
    assert spend["cost_usd"] == pytest.approx(_AGENT_USD)
    assert not (ctx.out / "results.csv").exists()  # a resumed run redoes it


def test_context7_refusing_the_key_at_connect_stops_the_run(
    tmp_path: Path, store: CorpusStore
) -> None:
    servers = [{"name": "docs", "status": "needs-auth"}]
    query = FakeQuery([_init("context7_live", mcp_servers=servers), _result()])

    with pytest.raises(HarnessError):
        run_session(_task(), "context7_live", _ctx(tmp_path, store, query))
    assert len(query.options) == 1  # not retried


def test_only_context7_can_stop_the_run(tmp_path: Path, store: CorpusStore) -> None:
    page = _session("full_page", tool_text="Invalid API key: what HTTP 401 means")

    record = run_session(_task(), "full_page", _ctx(tmp_path, store, FakeQuery(page)))

    assert record.correct is True


def test_a_webfetch_session_bills_the_page_reading_model_too(
    tmp_path: Path, store: CorpusStore
) -> None:
    reader = {"inputTokens": 20_000, "outputTokens": 300}
    usage = {MODEL: _AGENT_USAGE, WEBFETCH_MODEL: reader}
    query = FakeQuery(_session("webfetch_live", result=_result(model_usage=usage)))
    ctx = _ctx(tmp_path, store, query)

    record = run_session(_task(), "webfetch_live", ctx)

    reader_usd = (20_000 * 0.1 + 300 * 0.5) / 1e6
    assert record.cost_usd == pytest.approx(_AGENT_USD + reader_usd)
    assert record.input_tokens == 21_000
    (spend,) = _lines(ctx.out / "spend.jsonl")
    assert set(spend["usage"]) == {MODEL, WEBFETCH_MODEL}


def test_turn_limit_counts_as_incorrect_without_a_judge(
    tmp_path: Path, store: CorpusStore
) -> None:
    judge = ScriptedJudge(_CORRECT)
    limit = _result(subtype="error_max_turns", is_error=True, num_turns=15, result=None)
    query = FakeQuery(_session(result=limit))

    record = run_session(_task(), "full_page", _ctx(tmp_path, store, query, judge))

    assert record.hit_max_turns and record.correct is False
    assert not record.infra_error and judge.calls == 0


def test_a_prompt_over_the_context_window_counts_as_incorrect(
    tmp_path: Path, store: CorpusStore
) -> None:
    judge = ScriptedJudge(_CORRECT)
    too_long = _result(is_error=True, api_error_status=400, result="Prompt is too long")
    query = FakeQuery(_session(result=too_long))

    record = run_session(_task(), "full_page", _ctx(tmp_path, store, query, judge))

    assert record.correct is False and not record.infra_error and judge.calls == 0


def test_an_api_5xx_is_infrastructure(tmp_path: Path, store: CorpusStore) -> None:
    overloaded = _result(is_error=True, api_error_status=529, result="Overloaded")
    query = FakeQuery(_session(result=overloaded), _session())

    record = run_session(_task(), "full_page", _ctx(tmp_path, store, query))

    assert not record.infra_error and len(query.options) == 2


def test_an_unparseable_verdict_is_infrastructure(
    tmp_path: Path, store: CorpusStore
) -> None:
    judge = ScriptedJudge("not json")
    query = FakeQuery(_session())

    record = run_session(_task(), "full_page", _ctx(tmp_path, store, query, judge))

    assert record.infra_error and record.correct is None
    assert judge.calls == 3  # Amendment 5: 2 retries


def test_holdout_main_run_answers_feed_the_blind_labels(
    tmp_path: Path, store: CorpusStore
) -> None:
    query = FakeQuery(_session("nina"))
    ctx = _ctx(tmp_path, store, query)

    run_session(_task(split="holdout"), "nina", ctx)

    (row,) = _lines(ctx.out / "answers.jsonl")
    assert row == {
        "task_id": "so-1",
        "arm": "nina",
        "answer": "Use abs(x).",
        "judge_correct": True,
    }


def test_run_writes_rows_in_arm_order_and_stops_at_the_cap(
    tmp_path: Path, store: CorpusStore
) -> None:
    arms = ["full_page", "strong_rag"]
    query = FakeQuery(_session("full_page"))
    ctx = _ctx(tmp_path, store, query)
    query.runs = [_session(arm) for arm in arm_order("so-1") if arm in arms]

    run([_task()], arms, ctx, cap_usd=100.0)

    rows = (ctx.out / "results.csv").read_text(encoding="utf-8").splitlines()
    assert [r.split(",")[1] for r in rows[1:]] == [
        a for a in arm_order("so-1") if a in arms
    ]
    run([_task(), _task(id="so-2")], arms, ctx, cap_usd=0.001)  # already spent
    assert len((ctx.out / "results.csv").read_text().splitlines()) == 3


def test_usage_by_model_uses_the_canonical_model_id() -> None:
    result = _result(
        model_usage={
            "claude-sonnet-5-5[1m]": {
                "inputTokens": 7,
                "outputTokens": 1,
                "cacheReadInputTokens": 0,
                "cacheCreationInputTokens": 0,
                "canonicalModel": MODEL,
            }
        }
    )
    assert usage_by_model(result) == {
        MODEL: {
            "input_tokens": 7,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "output_tokens": 1,
        }
    }


class FailingJudge(ScriptedJudge):
    """A judge whose API call raises ``exc``."""

    def __init__(self, exc: Exception) -> None:
        super().__init__(_CORRECT)
        self.exc = exc

    def complete(self, prompt: str, **_kwargs: object) -> LLMResult:
        self.calls += 1
        raise self.exc


def test_a_judge_outage_is_infrastructure(tmp_path: Path, store: CorpusStore) -> None:
    judge = FailingJudge(TimeoutError("judge timed out"))
    ctx = _ctx(tmp_path, store, FakeQuery(_session()), judge)

    record = run_session(_task(), "full_page", ctx)

    assert record.infra_error and record.correct is None
    (row,) = _lines(ctx.out / "judgments.jsonl")
    assert row["correct"] is None and "TimeoutError" in row["reason"]


def test_a_refused_judge_key_stops_the_run(tmp_path: Path, store: CorpusStore) -> None:
    class Unauthorized(Exception):
        status_code = 401

    ctx = _ctx(tmp_path, store, FakeQuery(_session()), FailingJudge(Unauthorized()))
    with pytest.raises(HarnessError):
        run_session(_task(), "full_page", ctx)


def test_infra_tool_errors_are_found_behind_the_mcp_sdk_prefix() -> None:
    """The MCP SDK prefixes every tool error; detection must look past it."""
    import asyncio

    import httpx
    from mcp.server.mcpserver import MCPServer
    from mcp.server.mcpserver.exceptions import ToolError

    from app.mcp_server.client import NinaApiClient
    from app.mcp_server.server import build_server
    from bench.m10_agent import is_infra_tool_error
    from bench.m10_tools import INFRA_ERROR

    def wire_text(server: MCPServer, name: str, args: dict[str, object]) -> str:
        try:
            asyncio.run(server.call_tool(name, args))
        except ToolError as exc:
            return str(exc)  # the text the client receives
        raise AssertionError("expected a tool error")

    harness: MCPServer = MCPServer(name="docs")

    @harness.tool()
    def broken() -> str:
        raise ToolError(f"{INFRA_ERROR}: OSError")

    text = wire_text(harness, "broken", {})
    assert text.startswith("Error executing tool broken: ")
    assert is_infra_tool_error(text)

    nina = build_server(
        NinaApiClient(
            base_url="http://nina.local",
            api_key="k",
            transport=httpx.MockTransport(lambda r: httpx.Response(500)),
        )
    )
    text = wire_text(
        nina, "assemble_context", {"query": "q", "urls": ["https://a.test/"]}
    )
    assert is_infra_tool_error(text)

    assert not is_infra_tool_error("Error executing tool fetch_page: page not found: x")


def test_tasks_with_a_page_over_the_result_cap_leave_every_arm(
    store: CorpusStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import bench.m10_result_limits as limits
    from bench.m10_agent import drop_over_ceiling
    from bench.page_length import page_markdown

    size = len(page_markdown(store, _URL))
    monkeypatch.setattr(limits, "CEILING", size - 1)  # the fixture page is "too long"
    kept = drop_over_ceiling(
        [_task(), _task(id="so-2", urls=["https://example.com/missing"])],
        store,
        tmp_path,
    )
    assert [t.id for t in kept] == ["so-2"]  # unknown pages count as 0 chars
    record = json.loads((tmp_path / "excluded_over_ceiling.json").read_text())
    assert record == {"ceiling_chars": size - 1, "task_ids": ["so-1"]}


def _fake_git(trees: dict[str, str], dirty: str = "", run_commits: dict | None = None):
    def git(*args: str) -> str:
        if args[0] == "status":
            return dirty if args[-1] in dirty.split() else ""
        if args == ("rev-parse", "HEAD"):
            return "head123"
        ref = args[1]
        commit, _, name = ref.partition(":")
        if commit == "HEAD" or commit == "head123":
            return trees[name]
        return (run_commits or {})[commit]

    return git


def test_frozen_commit_accepts_only_the_frozen_trees(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import bench.m10_agent as agent

    frozen = dict(agent.FROZEN_TREES)
    monkeypatch.setattr(agent, "_git", _fake_git(frozen))
    assert agent.frozen_commit(tmp_path / "results.csv") == "head123"

    monkeypatch.setattr(agent, "_git", _fake_git({**frozen, "app": "changed"}))
    with pytest.raises(SystemExit, match="differs from the tree frozen"):
        agent.frozen_commit(tmp_path / "results.csv")

    monkeypatch.setattr(agent, "_git", _fake_git(frozen, dirty="migrations"))
    with pytest.raises(SystemExit, match="uncommitted"):
        agent.frozen_commit(tmp_path / "results.csv")


def test_frozen_commit_refuses_a_results_file_from_another_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import bench.m10_agent as agent

    results = tmp_path / "results.csv"
    results.write_text("task_id,git_commit\nso-1,old456\n", encoding="utf-8")
    git = _fake_git(dict(agent.FROZEN_TREES), run_commits={"old456": "other-tree"})
    monkeypatch.setattr(agent, "_git", git)
    with pytest.raises(SystemExit, match="another app"):
        agent.frozen_commit(results)


def test_frozen_trees_are_the_ones_the_prereg_names() -> None:
    import bench.m10_agent as agent

    text = (Path(__file__).resolve().parents[3] / "bench" / "PREREG.md").read_text(
        encoding="utf-8"
    )
    for tree in agent.FROZEN_TREES.values():
        assert tree in text


def test_drop_unhealthy_excludes_tasks_with_an_unusable_cited_snapshot(
    store: CorpusStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import bench.page_health as health
    from bench.m10_agent import drop_unhealthy

    monkeypatch.setattr(health, "EMPTY_CHARS", 20)  # the fixture page is tiny

    missing = "https://example.com/missing"
    kept = drop_unhealthy(
        [
            _task(),
            # Only cited pages count: a listed page outside the corpus is fine.
            _task(id="so-2", urls=[_URL, missing]),
            _task(id="so-3", urls=[missing], cited_urls=[missing]),
        ],
        store,
        tmp_path,
    )
    assert [t.id for t in kept] == ["so-1", "so-2"]
    record = json.loads((tmp_path / "excluded_unhealthy.json").read_text())
    assert record == {"so-3": {missing: "empty"}}
