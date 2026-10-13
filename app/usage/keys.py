"""API-key generation, hashing and the session-scoped repository — M9 (SPEC §7).

A key is minted as ``nina_<token_urlsafe(32)>`` and only its sha256 digest is
ever persisted; the plaintext is returned exactly ONCE by
:meth:`ApiKeyRepo.create` (shown by ``app/admin.py``) and never stored or logged
(CLAUDE.md: secrets live only in ``.env`` / the operator's hands, never in code,
tests or logs). Verification re-hashes the presented Bearer token with the same
sha256 and looks the digest up — the plaintext is never recoverable from the
database.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.store.models import ApiKey

#: Plaintext key prefix (SPEC §7). Lets an operator recognize a NINA key at a
#: glance; it is part of the signed material (hashed with the rest).
API_KEY_PREFIX = "nina_"


def generate_api_key() -> tuple[str, str]:
    """Return a fresh ``(plaintext, key_hash)`` pair.

    ``plaintext`` is ``nina_`` + 32 bytes of URL-safe randomness; ``key_hash``
    is its sha256 hex digest. Only the hash is ever persisted.
    """
    plaintext = f"{API_KEY_PREFIX}{secrets.token_urlsafe(32)}"
    return plaintext, hash_api_key(plaintext)


def hash_api_key(plaintext: str) -> str:
    """Return the sha256 hex digest of a plaintext key (same function used both
    to persist a new key and to look up a presented Bearer token)."""
    return hashlib.sha256(plaintext.encode()).hexdigest()


def make_api_key_id() -> str:
    """Return a new ``key_`` id."""
    return f"key_{secrets.token_hex(16)}"


def make_usage_event_id() -> str:
    """Return a new ``evt_`` id (used by FEAT-002 to key usage rows)."""
    return f"evt_{secrets.token_hex(16)}"


class ApiKeyRepo:
    """Session-scoped repository for API keys (same pattern as ``DocumentRepo``)."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self, name: str, limits: dict[str, object] | None = None
    ) -> tuple[ApiKey, str]:
        """Mint a key, persist ONLY its hash, and return ``(ApiKey, plaintext)``.

        The plaintext is returned to the caller exactly once (``app/admin.py``
        prints it, then it is unrecoverable). It is never stored or logged.
        """
        plaintext, key_hash = generate_api_key()
        api_key = ApiKey(
            id=make_api_key_id(),
            key_hash=key_hash,
            name=name,
            revoked_at=None,
            limits=limits if limits is not None else {},
        )
        self._session.add(api_key)
        self._session.flush()
        return api_key, plaintext

    def get_active_by_hash(self, key_hash: str) -> ApiKey | None:
        """Return the active (not revoked) key with this hash, or None."""
        stmt = select(ApiKey).where(
            ApiKey.key_hash == key_hash,
            ApiKey.revoked_at.is_(None),
        )
        return self._session.scalars(stmt).one_or_none()

    def revoke_by_name(self, name: str) -> ApiKey | None:
        """Revoke the active key with this name; return it, or None if absent.

        Only active keys are considered so a name reused after a revocation
        resolves to the live key.
        """
        stmt = select(ApiKey).where(
            ApiKey.name == name,
            ApiKey.revoked_at.is_(None),
        )
        api_key = self._session.scalars(stmt).one_or_none()
        if api_key is None:
            return None
        self._mark_revoked(api_key)
        return api_key

    def revoke_by_id(self, key_id: str) -> ApiKey | None:
        """Revoke the key with this id; return it, or None if absent/already revoked."""
        api_key = self._session.get(ApiKey, key_id)
        if api_key is None or api_key.revoked_at is not None:
            return None
        self._mark_revoked(api_key)
        return api_key

    def _mark_revoked(self, api_key: ApiKey) -> None:
        api_key.revoked_at = datetime.now(UTC)
        self._session.flush()
