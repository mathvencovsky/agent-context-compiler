"""Offline unit tests for API-key generation, hashing and ``ApiKeyRepo`` — M9."""

from __future__ import annotations

from sqlalchemy.orm import Session, sessionmaker

from app.usage.keys import (
    API_KEY_PREFIX,
    ApiKeyRepo,
    generate_api_key,
    hash_api_key,
    make_api_key_id,
    make_usage_event_id,
)


def test_generate_api_key_has_prefix_and_matching_hash() -> None:
    """The plaintext is ``nina_``-prefixed and its hash matches ``hash_api_key``."""
    plaintext, key_hash = generate_api_key()
    assert plaintext.startswith(API_KEY_PREFIX)
    assert hash_api_key(plaintext) == key_hash
    # sha256 hex digest is 64 chars; the plaintext is NOT the hash.
    assert len(key_hash) == 64
    assert plaintext != key_hash


def test_generate_api_key_is_random() -> None:
    """Two mints never collide (plaintext and hash both differ)."""
    first_plain, first_hash = generate_api_key()
    second_plain, second_hash = generate_api_key()
    assert first_plain != second_plain
    assert first_hash != second_hash


def test_id_helpers_prefixes_and_uniqueness() -> None:
    """``make_api_key_id``/``make_usage_event_id`` carry their prefixes and vary."""
    assert make_api_key_id().startswith("key_")
    assert make_usage_event_id().startswith("evt_")
    assert make_api_key_id() != make_api_key_id()
    assert make_usage_event_id() != make_usage_event_id()


def test_create_persists_only_hash(session_factory: sessionmaker[Session]) -> None:
    """``create`` stores the hash (never the plaintext) and returns the plaintext."""
    session = session_factory()
    try:
        repo = ApiKeyRepo(session)
        api_key, plaintext = repo.create("piloto-x", {"rpm": 60})
        session.commit()

        assert plaintext.startswith(API_KEY_PREFIX)
        assert api_key.id.startswith("key_")
        assert api_key.key_hash == hash_api_key(plaintext)
        # The stored row never carries the plaintext anywhere.
        assert api_key.key_hash != plaintext
        assert api_key.limits == {"rpm": 60}
        assert api_key.revoked_at is None
    finally:
        session.close()


def test_get_active_by_hash_finds_and_filters_revoked(
    session_factory: sessionmaker[Session],
) -> None:
    """An active key is found by hash; a revoked one is not."""
    session = session_factory()
    try:
        repo = ApiKeyRepo(session)
        api_key, plaintext = repo.create("piloto-x")
        session.commit()

        found = repo.get_active_by_hash(hash_api_key(plaintext))
        assert found is not None
        assert found.id == api_key.id

        repo.revoke_by_id(api_key.id)
        session.commit()
        assert repo.get_active_by_hash(hash_api_key(plaintext)) is None
    finally:
        session.close()


def test_revoke_by_name_and_by_id(session_factory: sessionmaker[Session]) -> None:
    """Revoke by name and by id both set ``revoked_at``; absent targets return None."""
    session = session_factory()
    try:
        repo = ApiKeyRepo(session)
        by_name, _ = repo.create("name-key")
        by_id, _ = repo.create("id-key")
        session.commit()

        revoked_name = repo.revoke_by_name("name-key")
        assert revoked_name is not None
        assert revoked_name.revoked_at is not None

        revoked_id = repo.revoke_by_id(by_id.id)
        assert revoked_id is not None
        assert revoked_id.revoked_at is not None

        # Nonexistent targets return None (the caller surfaces the failure).
        assert repo.revoke_by_name("does-not-exist") is None
        assert repo.revoke_by_id("key_missing") is None
    finally:
        session.close()


def test_get_active_by_hash_unknown_returns_none(
    session_factory: sessionmaker[Session],
) -> None:
    """An unknown hash yields None (no partial match, no error)."""
    session = session_factory()
    try:
        assert ApiKeyRepo(session).get_active_by_hash("deadbeef") is None
    finally:
        session.close()
