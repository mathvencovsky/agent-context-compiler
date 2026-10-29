"""Transcript lines in the shape bench.m10_agent writes them, for tests."""

from __future__ import annotations

import json


def transcript(results: list[tuple[str, str]], final: str | None) -> list[str]:
    """One tool call and its result per ``(tool, text)``, then the result message."""
    lines = [json.dumps({"type": "SystemMessage", "subtype": "init", "data": {}})]
    for i, (tool, text) in enumerate(results):
        lines.append(
            json.dumps(
                {
                    "type": "AssistantMessage",
                    "content": [{"id": f"t{i}", "name": tool, "input": {}}],
                }
            )
        )
        lines.append(
            json.dumps(
                {
                    "type": "UserMessage",
                    "content": [
                        {
                            "tool_use_id": f"t{i}",
                            "content": [{"type": "text", "text": text}],
                            "is_error": False,
                        }
                    ],
                }
            )
        )
    if final is not None:
        lines.append(json.dumps({"type": "ResultMessage", "result": final}))
    return lines
