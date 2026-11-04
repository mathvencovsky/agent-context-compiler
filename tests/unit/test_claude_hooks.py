"""The Claude Code hook that asks before a shell command turns paid calls on.

Runs the script as Claude Code does: JSON on stdin, decision on stdout. The
last tests read `.claude/settings.json`, because a wrong matcher or script
path there turns the guard off silently (a failing hook does not block).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HOOK = ROOT / ".claude" / "hooks" / "paid_calls_guard.py"
SETTINGS = ROOT / ".claude" / "settings.json"


def run_hook(stdin: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def shell_event(command: str, tool: str = "Bash") -> str:
    return json.dumps(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": tool,
            "tool_input": {"command": command, "description": "x"},
        }
    )


def assert_asks(result: subprocess.CompletedProcess[str]) -> None:
    assert result.returncode == 0
    output = json.loads(result.stdout)["hookSpecificOutput"]
    assert output["hookEventName"] == "PreToolUse"
    assert output["permissionDecision"] == "ask"
    assert "ALLOW_PAID_CALLS" in output["permissionDecisionReason"]


@pytest.mark.parametrize(
    "command",
    [
        "ALLOW_PAID_CALLS=true make bench ARGS='--real'",
        "env ALLOW_PAID_CALLS=1 uv run python -m bench.m10_agent",
        "export ALLOW_PAID_CALLS=TRUE && make m10a-pending",
        "sed -i 's/ALLOW_PAID_CALLS=false/ALLOW_PAID_CALLS=true/' .env",
        "ALLOW_PAID_CALLS = yes uv run python x.py",
        "allow_paid_calls=on make bench",
        "ALLOW_PAID_CALLS=t make bench",
        "ALLOW_PAID_CALLS=Y make bench",
    ],
)
def test_bash_commands_that_turn_paid_calls_on_ask_the_human(command: str) -> None:
    assert_asks(run_hook(shell_event(command)))


@pytest.mark.parametrize(
    "command",
    [
        '$env:ALLOW_PAID_CALLS="true"; uv run python -m bench.m10_agent',
        "$env:ALLOW_PAID_CALLS = '1'; make bench",
    ],
)
def test_powershell_commands_that_turn_paid_calls_on_ask_the_human(
    command: str,
) -> None:
    assert_asks(run_hook(shell_event(command, tool="PowerShell")))


@pytest.mark.parametrize("tool", ["Bash", "PowerShell"])
@pytest.mark.parametrize(
    "command",
    [
        "make check",
        "grep -rn ALLOW_PAID_CALLS app",
        "ALLOW_PAID_CALLS=false make bench",
        "ALLOW_PAID_CALLS=0 uv run python -m bench.run",
        "ALLOW_PAID_CALLS=truely_not_a_bool make bench",
    ],
)
def test_other_commands_get_no_decision(command: str, tool: str) -> None:
    result = run_hook(shell_event(command, tool=tool))

    assert result.returncode == 0
    assert result.stdout == ""


@pytest.mark.parametrize(
    "stdin",
    [
        "not json",
        "[]",
        json.dumps(
            {"tool_name": "Edit", "tool_input": {"command": "ALLOW_PAID_CALLS=1"}}
        ),
        json.dumps({"tool_name": "Bash", "tool_input": "ALLOW_PAID_CALLS=1"}),
        json.dumps({"tool_name": "Bash", "tool_input": {"command": None}}),
    ],
)
def test_unexpected_input_makes_no_decision(stdin: str) -> None:
    result = run_hook(stdin)

    assert result.returncode == 0
    assert result.stdout == ""


def configured_hook() -> tuple[str, dict[str, object]]:
    """The one PreToolUse hook in the project settings: (matcher, handler)."""
    settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
    groups = settings["hooks"]["PreToolUse"]
    assert len(groups) == 1
    handlers = groups[0]["hooks"]
    assert len(handlers) == 1
    return groups[0]["matcher"], handlers[0]


@pytest.mark.parametrize("tool", ["Bash", "PowerShell"])
def test_settings_matcher_covers_every_shell_tool(tool: str) -> None:
    matcher, _ = configured_hook()

    assert re.fullmatch(matcher, tool)


def test_settings_runs_this_hook_and_its_decision_reaches_claude_code() -> None:
    """Run the exact argv from settings.json, placeholder substituted.

    Fails if `uv` is missing: the hook would then fail open in that
    environment too, and that is worth knowing.
    """
    _, handler = configured_hook()
    assert handler["type"] == "command"
    args = handler["args"]
    assert isinstance(args, list)
    argv = [str(handler["command"])] + [
        str(a).replace("${CLAUDE_PROJECT_DIR}", str(ROOT)) for a in args
    ]
    assert Path(argv[-1]).resolve() == HOOK

    result = subprocess.run(
        argv,
        input=shell_event("ALLOW_PAID_CALLS=true make bench", tool="PowerShell"),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert_asks(result)
