"""Bearer API-key authentication for the /v1 routes — M9 (SPEC §7/§8).

:func:`require_api_key` is the FastAPI dependency attached to every /v1 router.
It is a NO-OP when ``settings.env == "dev"`` (so the M0-M8 tests, which run in
dev, keep working without a header) and ENFORCED otherwise: it reads
``Authorization: Bearer <key>``, re-hashes the token with sha256, and looks up an
active key via :class:`~app.usage.keys.ApiKeyRepo`. A missing/malformed header,
an unknown key, or a revoked key all raise :class:`~app.api.errors.Unauthorized`
(401, serialized by the global handler).

The authenticated :class:`ApiKey` (or ``None`` in dev) is stashed on
``request.state.api_key`` for FEAT-002 to read when it writes usage events.

Like the other API collaborators, the session factory and settings are exposed
as overridable FastAPI ``Depends`` seams so the integration tests can swap in the
test database and flip ``env`` to ``production`` via ``app.dependency_overrides``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session, sessionmaker

from app.api.errors import Unauthorized
from app.config import Settings, get_settings
from app.store.models import ApiKey
from app.store.repo import make_engine, make_session_factory
from app.usage.keys import ApiKeyRepo, hash_api_key

#: Name of the request.state attribute carrying the authenticated key.
REQUEST_STATE_API_KEY = "api_key"

_BEARER_PREFIX = "Bearer "


@dataclass
class AuthPrincipal:
    """The authenticated caller. ``api_key`` is ``None`` in dev (no-op auth)."""

    api_key: ApiKey | None


# --------------------------------------------------------------------------- #
# Dependency seams (overridable in tests via app.dependency_overrides)
# --------------------------------------------------------------------------- #
def provide_settings() -> Settings:
    """Return the process settings (overridable in tests)."""
    return get_settings()


def provide_auth_session_factory() -> sessionmaker[Session]:
    """Build a session factory bound to the configured database (overridable)."""
    return make_session_factory(make_engine(get_settings()))


AuthSettingsDep = Annotated[Settings, Depends(provide_settings)]
AuthSessionFactoryDep = Annotated[
    sessionmaker[Session], Depends(provide_auth_session_factory)
]


def _bearer_token(request: Request) -> str:
    """Extract the Bearer token, raising :class:`Unauthorized` if absent/malformed."""
    header = request.headers.get("authorization")
    if not header or not header.startswith(_BEARER_PREFIX):
        raise Unauthorized("missing or malformed Authorization header")
    token = header[len(_BEARER_PREFIX) :].strip()
    if not token:
        raise Unauthorized("missing or malformed Authorization header")
    return token


def require_api_key(
    request: Request,
    settings: AuthSettingsDep,
    session_factory: AuthSessionFactoryDep,
) -> AuthPrincipal:
    """Authenticate the caller's Bearer API key (no-op in dev).

    Stores the authenticated :class:`ApiKey` (or ``None`` in dev) on
    ``request.state.api_key`` for the usage layer (FEAT-002).
    """
    if settings.env == "dev":
        principal = AuthPrincipal(api_key=None)
        setattr(request.state, REQUEST_STATE_API_KEY, None)
        return principal

    token = _bearer_token(request)
    key_hash = hash_api_key(token)

    session = session_factory()
    try:
        api_key = ApiKeyRepo(session).get_active_by_hash(key_hash)
    finally:
        session.close()

    if api_key is None:
        raise Unauthorized("invalid or revoked API key")

    setattr(request.state, REQUEST_STATE_API_KEY, api_key)
    return AuthPrincipal(api_key=api_key)
