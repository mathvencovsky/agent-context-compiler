"""PreToolUse hook: ask the human before a Bash command turns paid calls on.

The code-level lock is ``ALLOW_PAID_CALLS`` (default false; DECISIONS
2026-11-04). This hook is a second layer at the tool boundary: when a Bash
command sets that variable to a true value (inline, by ``export``, or by
editing ``.env`` with sed), Claude Code asks the human before running it.

It cannot see a value set some other way (a script that opens ``.env`` itself,
a variable already exported in the shell). Any parsing failure makes no
decision: the code-level lock still holds.
"""

from __future__ import annotations

import json
import re
import sys

# The values pydantic-settings reads as true, case-insensitively.
_TURNS_ON = re.compile(
    r"ALLOW_PAID_CALLS[\"']?\s*[:=]\s*[\"']?(?:1|true|yes|on|t|y)\b",
    re.IGNORECASE,
)

REASON = (
    "This command turns ALLOW_PAID_CALLS on, so it can spend money on paid "
    "APIs. Paid runs need the human's approval and a stated expected cost "
    "(CLAUDE.md, DECISIONS 2026-11-04)."
)


def decide(event: dict[str, object]) -> dict[str, object] | None:
    """Return the hook output for one PreToolUse event, or None for no decision."""
    if event.get("tool_name") != "Bash":
        return None
    tool_input = event.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    command = tool_input.get("command")
    if not isinstance(command, str) or not _TURNS_ON.search(command):
        return None
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": REASON,
        }
    }


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return 0
    if not isinstance(event, dict):
        return 0
    output = decide(event)
    if output is not None:
        json.dump(output, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
