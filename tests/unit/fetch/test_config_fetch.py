"""New M1 config fields: port allow-list, robots TTL, computed User-Agent."""

from __future__ import annotations

import pytest

from app.config import Settings


def test_defaults() -> None:
    s = Settings()
    assert s.allowed_ports == (80, 443)
    assert s.robots_cache_ttl_seconds == 86400


def test_allowed_ports_parsed_from_comma_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALLOWED_PORTS", "80,443,8443")
    s = Settings()
    assert s.allowed_ports == (80, 443, 8443)


def test_allowed_ports_strips_whitespace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOWED_PORTS", " 80 , 8080 ")
    s = Settings()
    assert s.allowed_ports == (80, 8080)


def test_user_agent_contains_contact_url() -> None:
    s = Settings(fetcher_contact_url="https://nina.example/contact")
    ua = s.user_agent()
    assert ua == "AgentContextBot/0.1 (+https://nina.example/contact)"
    assert "https://nina.example/contact" in ua
