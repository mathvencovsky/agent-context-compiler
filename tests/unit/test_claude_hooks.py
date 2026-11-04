"""The Claude Code hook that asks before a Bash command turns paid calls on.

Runs the script as Claude Code does: JSON on stdin, decision on stdout.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[2] / ".claude" / "hooks" / "paid_calls_guard.py"


def run_hook(stdin: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def bash_event(command: str) -> str:
    return json.dumps(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": command, "description": "x"},
        }
    )


@pytest.mark.parametrize(
    "command",
    [
        "ALLOW_PAID_CALLS=true make bench ARGS='--real'",
        "env ALLOW_PAID_CALLS=1 uv run python -m bench.m10_agent",
        "export ALLOW_PAID_CALLS=TRUE && make m10a-pending",
        "sed -i 's/ALLOW_PAID_CALLS=false/ALLOW_PAID_CALLS=true/' .env",
        '$env:ALLOW_PAID_CALLS="true"; uv run python -m bench.run',
        "ALLOW_PAID_CALLS = yes uv run python x.py",
    ],
)
def test_commands_that_turn_paid_calls_on_ask_the_human(command: str) -> None:
    result = run_hook(bash_event(command))

    assert result.returncode == 0
    output = json.loads(result.stdout)["hookSpecificOutput"]
    assert output["hookEventName"] == "PreToolUse"
    assert output["permissionDecision"] == "ask"
    assert "ALLOW_PAID_CALLS" in output["permissionDecisionReason"]


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
def test_other_commands_get_no_decision(command: str) -> None:
    result = run_hook(bash_event(command))

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
