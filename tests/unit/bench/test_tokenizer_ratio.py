"""Claude-vs-cl100k token ratio report (bench/tokenizer_ratio.py), offline."""

from __future__ import annotations

from app.index.tokens import count_tokens
from bench.tokenizer_ratio import kind_of, measure, pick, table


def test_kind_of_splits_fenced_code_from_prose() -> None:
    assert kind_of("Use it.\n\n```python\nx = 1\n```") == "code"
    assert kind_of("Plain prose.") == "prose"


def test_pick_is_seeded_and_capped_per_site() -> None:
    chunks = {"mdn": [f"m{i}" for i in range(10)], "python": ["p0", "p1", " "]}
    first = pick(chunks, per_site=3)
    assert first == pick(chunks, per_site=3)
    assert sum(site == "mdn" for site, _ in first) == 3
    assert sorted(md for site, md in first if site == "python") == ["p0", "p1"]


def test_table_reports_ratio_of_totals_and_the_budget_it_implies() -> None:
    picked = [("python", "Plain prose here."), ("python", "```\nx = 1\n```")]
    samples = measure(picked, lambda text: 2 * count_tokens(text))
    report = table(samples)
    assert "| all | 2 |" in report
    assert "| 2.000 | 2.000 |" in report
    assert "about 8000 Claude tokens" in report
    assert "| kind: code | 1 |" in report and "| site: python | 2 |" in report


def test_nina_tools_are_the_three_mcp_tools_in_messages_api_shape() -> None:
    from bench.tokenizer_ratio import nina_tools, tools_line

    tools = nina_tools()
    assert [t["name"] for t in tools] == [
        "resolve_url",
        "assemble_context",
        "get_document",
    ]
    assert all({"name", "description", "input_schema"} == set(t) for t in tools)
    assert tools_line(735, 900) == (
        "NINA MCP tool definitions, sent on every turn: 735 cl100k tokens, "
        "900 Claude tokens."
    )
