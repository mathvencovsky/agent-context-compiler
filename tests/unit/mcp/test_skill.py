"""The agent skill stays consistent with the MCP server (skills/nina-docs)."""

from __future__ import annotations

import re
from pathlib import Path

from app.mcp_server.client import NinaApiClient
from app.mcp_server.server import build_server

SKILL = Path(__file__).resolve().parents[3] / "skills" / "nina-docs" / "SKILL.md"


async def test_skill_names_only_real_tools_and_hint_codes() -> None:
    text = SKILL.read_text(encoding="utf-8")
    server = build_server(client=NinaApiClient(base_url="http://x", api_key="k"))
    tools = {t.name for t in await server.list_tools()}
    named = set(re.findall(r"`([a-z_]+)\(", text))
    assert named and named <= tools
    for code in ("source_failed", "low_coverage", "budget_limited", "mixed_versions"):
        assert f"`{code}`" in text


def test_skill_frontmatter_and_size() -> None:
    text = SKILL.read_text(encoding="utf-8")
    front = text.split("---")[1]
    assert "name: nina-docs" in front and "description:" in front
    assert len(text.split()) < 600  # loaded into context when the skill triggers
