"""Offline unit tests for the ``app.admin`` create-key/revoke-key CLI — M9.

The CLI's database is swapped for the in-memory SQLite ``session_factory``
fixture by patching ``app.admin.make_session_factory`` (and no-opping
``make_engine``), so no services and no network are touched (CLAUDE.md). The
tests assert the plaintext is printed exactly once and that nothing logs it.
"""

from __future__ import annotations

import logging

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app import admin
from app.usage.keys import ApiKeyRepo, hash_api_key


@pytest.fixture(autouse=True)
def _patch_db(
    monkeypatch: pytest.MonkeyPatch, session_factory: sessionmaker[Session]
) -> None:
    monkeypatch.setattr(admin, "make_engine", lambda settings: None)
    monkeypatch.setattr(admin, "make_session_factory", lambda engine: session_factory)


def test_create_key_prints_plaintext_once_and_persists_hash(
    capsys: pytest.CaptureFixture[str],
    session_factory: sessionmaker[Session],
) -> None:
    """create-key prints a ``nina_`` key exactly once and stores only its hash."""
    exit_code = admin.main(["create-key", "--name", "piloto-x"])
    assert exit_code == 0

    out = capsys.readouterr().out
    key_lines = [line for line in out.splitlines() if line.startswith("nina_")]
    assert len(key_lines) == 1, out
    plaintext = key_lines[0]

    # The persisted row carries only the hash of what was printed.
    session = session_factory()
    try:
        stored = ApiKeyRepo(session).get_active_by_hash(hash_api_key(plaintext))
        assert stored is not None
        assert stored.name == "piloto-x"
        assert stored.key_hash != plaintext
    finally:
        session.close()


def test_create_key_with_limits(session_factory: sessionmaker[Session]) -> None:
    """``--limits`` JSON is persisted on the key."""
    assert admin.main(["create-key", "--name", "lim", "--limits", '{"rpm": 30}']) == 0
    session = session_factory()
    try:
        from sqlalchemy import select

        from app.store.models import ApiKey

        stored = session.scalars(select(ApiKey).where(ApiKey.name == "lim")).one()
        assert stored.limits == {"rpm": 30}
    finally:
        session.close()


def test_revoke_key_by_name(
    capsys: pytest.CaptureFixture[str], session_factory: sessionmaker[Session]
) -> None:
    """revoke-key --name revokes the active key."""
    admin.main(["create-key", "--name", "piloto-x"])
    capsys.readouterr()  # drop the create output

    assert admin.main(["revoke-key", "--name", "piloto-x"]) == 0

    session = session_factory()
    try:
        from sqlalchemy import select

        from app.store.models import ApiKey

        stored = session.scalars(select(ApiKey).where(ApiKey.name == "piloto-x")).one()
        assert stored.revoked_at is not None
    finally:
        session.close()


def test_revoke_key_by_id(session_factory: sessionmaker[Session]) -> None:
    """revoke-key --id revokes the key with that id."""
    session = session_factory()
    try:
        api_key, _ = ApiKeyRepo(session).create("by-id")
        session.commit()
        key_id = api_key.id
    finally:
        session.close()

    assert admin.main(["revoke-key", "--id", key_id]) == 0

    session = session_factory()
    try:
        from app.store.models import ApiKey

        assert session.get(ApiKey, key_id).revoked_at is not None
    finally:
        session.close()


def test_revoke_nonexistent_name_fails(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Revoking an unknown name exits != 0 with a clear message."""
    exit_code = admin.main(["revoke-key", "--name", "ghost"])
    assert exit_code != 0
    assert "no active API key" in capsys.readouterr().err


def test_create_key_never_logs_the_plaintext(
    capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    """The plaintext key is printed but NEVER written to any log record."""
    with caplog.at_level(logging.DEBUG):
        admin.main(["create-key", "--name", "piloto-x"])

    out = capsys.readouterr().out
    plaintext = next(line for line in out.splitlines() if line.startswith("nina_"))
    for record in caplog.records:
        assert plaintext not in record.getMessage()
