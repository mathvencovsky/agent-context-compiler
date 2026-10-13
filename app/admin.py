"""Admin CLI for issuing and revoking API keys — M9 (SPEC §7).

Usage::

    python -m app.admin create-key --name piloto-x [--limits '{"rpm": 60}']
    python -m app.admin revoke-key (--name piloto-x | --id key_...)

``create-key`` mints a key via :class:`~app.usage.keys.ApiKeyRepo`, persists ONLY
its sha256 hash, and prints the plaintext to stdout ONCE with an explicit warning
that it will not be shown again. The plaintext is NEVER logged (CLAUDE.md:
secrets never land in logs). ``revoke-key`` revokes by name or id and fails
loudly (exit code != 0) when the target does not exist.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from app.config import get_settings
from app.store.models import ApiKey
from app.store.repo import make_engine, make_session_factory
from app.usage.keys import ApiKeyRepo


def _parse_limits(raw: str | None) -> dict[str, object]:
    """Parse the optional ``--limits`` JSON object, failing loudly on garbage."""
    if raw is None:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"error: --limits is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise SystemExit("error: --limits must be a JSON object")
    return parsed


def _create_key(args: argparse.Namespace) -> int:
    """Mint a key, print the plaintext ONCE, and persist only its hash."""
    limits = _parse_limits(args.limits)
    settings = get_settings()
    session_factory = make_session_factory(make_engine(settings))
    session = session_factory()
    try:
        repo = ApiKeyRepo(session)
        api_key, plaintext = repo.create(args.name, limits)
        # Capture before commit expires the instance / the session is closed.
        key_id = api_key.id
        key_name = api_key.name
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    # Printed to stdout ONCE; never logged. Store it now — it cannot be shown
    # again (only the hash is persisted).
    print(f"API key created for {key_name!r} (id {key_id}):")
    print(plaintext)
    print("Store it now — it will NOT be shown again.")
    return 0


def _revoke_key(args: argparse.Namespace) -> int:
    """Revoke a key by name or id; exit != 0 if the target does not exist."""
    settings = get_settings()
    session_factory = make_session_factory(make_engine(settings))
    session = session_factory()
    try:
        repo = ApiKeyRepo(session)
        revoked: ApiKey | None
        if args.id is not None:
            revoked = repo.revoke_by_id(args.id)
            target = f"id {args.id}"
        else:
            revoked = repo.revoke_by_name(args.name)
            target = f"name {args.name!r}"
        # Capture before commit expires the instance / the session is closed.
        revoked_id = revoked.id if revoked is not None else None
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    if revoked_id is None:
        print(f"error: no active API key with {target}", file=sys.stderr)
        return 1
    print(f"revoked API key {revoked_id} ({target}).")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build the ``app.admin`` argument parser."""
    parser = argparse.ArgumentParser(prog="app.admin", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create-key", help="issue a new API key")
    create.add_argument("--name", required=True, help="human label for the key")
    create.add_argument(
        "--limits",
        default=None,
        help="optional per-key limits as a JSON object, e.g. '{\"rpm\": 60}'",
    )
    create.set_defaults(func=_create_key)

    revoke = sub.add_parser("revoke-key", help="revoke an existing API key")
    group = revoke.add_mutually_exclusive_group(required=True)
    group.add_argument("--name", help="revoke the active key with this name")
    group.add_argument("--id", help="revoke the key with this id (key_...)")
    revoke.set_defaults(func=_revoke_key, name=None, id=None)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point: parse args and dispatch to the sub-command."""
    parser = build_parser()
    args = parser.parse_args(argv)
    result: int = args.func(args)
    return result


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
