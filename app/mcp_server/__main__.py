"""Entry point for the NINA MCP server (SPEC §9).

Run over stdio (default, for a local agent) or streamable HTTP (for remote use):

    python -m app.mcp_server                      # stdio
    python -m app.mcp_server --http               # streamable HTTP
    python -m app.mcp_server --transport streamable-http
    MCP_TRANSPORT=streamable-http python -m app.mcp_server

Streamable HTTP binds to 127.0.0.1 only and validates the ``Host`` and
``Origin`` headers (DNS-rebinding protection, an MCP specification MUST). It
has NO authentication of its own: anyone who can reach it uses the server's
``API_KEY``. Exposing it beyond loopback requires an authenticating proxy (or
MCP OAuth) in front; it is not supported as is.

``API_URL`` and ``API_KEY`` must be set in the environment; the server fails
loudly (exit code 2) before serving if either is missing, rather than starting
and erroring on the first tool call.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Literal

from mcp.server.transport_security import TransportSecuritySettings

from app.mcp_server.client import MissingApiConfig, NinaApiClient
from app.mcp_server.server import build_server

#: Env var selecting the transport when no CLI flag is given.
TRANSPORT_ENV = "MCP_TRANSPORT"

Transport = Literal["stdio", "streamable-http"]

_STDIO: Transport = "stdio"
_HTTP: Transport = "streamable-http"


#: Streamable HTTP is served on loopback only (see module docstring).
HTTP_HOST = "127.0.0.1"


def http_transport_security() -> TransportSecuritySettings:
    """Host/Origin allow-lists for the loopback HTTP transport.

    Stated explicitly rather than relying on the SDK's implicit default, so an
    SDK change cannot silently turn the DNS-rebinding protection off.
    """
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*"],
        allowed_origins=["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"],
    )


def _parse_args(argv: list[str]) -> Transport:
    """Return the chosen transport ("stdio" or "streamable-http")."""
    parser = argparse.ArgumentParser(
        prog="python -m app.mcp_server",
        description="NINA MCP server — a thin client of the NINA HTTP API.",
    )
    parser.add_argument(
        "--transport",
        choices=[_STDIO, _HTTP],
        default=None,
        help=f"transport to serve on (default: {_STDIO}, or ${TRANSPORT_ENV}).",
    )
    parser.add_argument(
        "--http",
        action="store_true",
        help=f"shorthand for --transport {_HTTP}.",
    )
    args = parser.parse_args(argv)

    if args.transport is not None:
        # argparse restricts the value to the choices, so it is a valid Transport.
        return _HTTP if args.transport == _HTTP else _STDIO
    if args.http:
        return _HTTP
    return _HTTP if os.environ.get(TRANSPORT_ENV) == _HTTP else _STDIO


def main(argv: list[str] | None = None) -> int:
    """Build the server from the environment and run the chosen transport."""
    transport = _parse_args(sys.argv[1:] if argv is None else argv)

    # Resolve the client up front so a missing API_URL/API_KEY fails BEFORE we
    # start serving, instead of surfacing on the first tool call.
    try:
        client = NinaApiClient.from_env()
    except MissingApiConfig as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    # NINA_SCOPE=web: opt-in, any public page (DECISIONS 2026-11-03).
    server = build_server(client=client, web=os.environ.get("NINA_SCOPE") == "web")
    # Dispatch on the concrete literal so the correct run() overload is selected.
    if transport == _HTTP:
        server.run(
            transport="streamable-http",
            host=HTTP_HOST,
            transport_security=http_transport_security(),
        )
    else:
        server.run(transport=_STDIO)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
