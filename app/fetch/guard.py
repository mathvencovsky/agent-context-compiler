"""SSRF guard — URL/IP validation and port policy (SPEC §3.1, M1).

This module is the ONLY place that decides whether an outbound destination is
legal. The design closes the DNS-rebinding window by resolving the hostname to
every A/AAAA record through an *injectable* resolver, validating EACH resolved
IP, and returning one validated IP. The client then connects on that literal IP
while restoring the ``Host`` header and TLS SNI to the original hostname, so no
second DNS resolution ever happens (see ``app/fetch/client.py``).

Validation is fail-closed: a single non-global address among the resolved set
rejects the whole host. ``ipaddress.is_global`` is NOT trusted on its own —
loopback, link-local, private, reserved, multicast, unspecified, the cloud
metadata address ``169.254.169.254`` and the CGNAT range ``100.64.0.0/10`` are
all rejected explicitly, and IPv4-mapped / NAT64 IPv6 forms are unwrapped to the
embedded IPv4 and revalidated.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit, urlunsplit

from app.config import Settings, get_settings
from app.fetch.errors import BlockedDestination, FetchFailed, InvalidURL

_ALLOWED_SCHEMES = frozenset({"http", "https"})
_DEFAULT_PORTS = {"http": 80, "https": 443}

# NAT64 well-known prefix (RFC 6052): the last 32 bits embed an IPv4 address.
_NAT64_PREFIX = ipaddress.ip_network("64:ff9b::/96")
# 6to4 (RFC 3056): 2002:V4ADDR::/48 embeds an IPv4 address in bits 16..47.
_SIXTOFOUR_PREFIX = ipaddress.ip_network("2002::/16")
# Carrier-grade NAT (RFC 6598). Checked explicitly for defense in depth.
_CGNAT_NET = ipaddress.ip_network("100.64.0.0/10")
# Cloud metadata endpoints. 169.254.169.254 (AWS/GCP/Azure/OCI IMDS) is also
# link-local; 168.63.129.16 is Azure's WireServer/metadata, a PUBLIC address
# that is_global accepts, so it must be listed (security research 2026-10-28).
_METADATA_V4 = frozenset(
    {ipaddress.ip_address("169.254.169.254"), ipaddress.ip_address("168.63.129.16")}
)
# Deprecated IPv6 site-local (RFC 3879): stdlib still reports it as global.
_SITE_LOCAL_V6 = ipaddress.ip_network("fec0::/10")


class Resolver(Protocol):
    """Injectable DNS resolver seam.

    ``resolve`` returns the IP strings (A and AAAA) a hostname maps to. The real
    implementation calls the OS resolver; tests inject a deterministic fake so
    no suite ever touches real DNS.
    """

    def resolve(self, host: str) -> list[str]: ...


class SocketResolver:
    """Real resolver backed by :func:`socket.getaddrinfo` (A and AAAA)."""

    def resolve(self, host: str) -> list[str]:
        try:
            infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
        except socket.gaierror:
            return []
        seen: list[str] = []
        for info in infos:
            # sockaddr[0] is the address; it is a str for AF_INET/AF_INET6.
            ip = str(info[4][0])
            # getaddrinfo may append an IPv6 scope id ("fe80::1%eth0"); drop it.
            ip = ip.split("%", 1)[0]
            if ip not in seen:
                seen.append(ip)
        return seen


@dataclass(frozen=True)
class GuardResult:
    """Outcome of a successful validation.

    ``validated_ip`` is the literal IP the client must connect to; ``hostname``
    is the original name used for the ``Host`` header and TLS SNI.
    """

    normalized_url: str
    hostname: str
    validated_ip: str
    port: int


def _is_forbidden_ip_literal(host: str) -> bool:
    """True if ``host`` is an IPv4 literal written in an alternate notation.

    SPEC §3.1 requires rejecting decimal (``2130706433``), octal and
    hexadecimal (``0x7f000001``) IPv4 literals, as well as short/packed dotted
    forms (``127.1``) that ``inet_aton`` would expand — these bypass per-octet
    reasoning and are rejected BEFORE any DNS resolution.

    A plain, well-formed dotted-quad (``192.0.2.1``) is NOT forbidden here; it
    returns False and is still IP-validated later. Bracketed IPv6 literals are
    handled by the caller before this function is reached.
    """
    # Pure integer: decimal, hex (0x...) or octal (0o... / leading-zero).
    lowered = host.lower()
    try:
        if lowered.startswith("0x"):
            int(lowered, 16)
            return True
        if lowered.startswith("0o"):
            int(lowered, 8)
            return True
        # All-digits decimal integer (e.g. 2130706433). A normal dotted-quad
        # contains dots, so this only catches the packed-integer form.
        if host.isdigit():
            # A leading zero signals octal to inet_aton; either way it is a
            # non-dotted-quad literal and forbidden.
            return True
    except ValueError:
        pass

    # Dotted forms with an alternate base or fewer than four parts. A canonical
    # dotted-quad is four base-10 octets each in 0..255; anything else that
    # still parses as a packed IPv4 literal is forbidden.
    if "." in host and not host.endswith("."):
        parts = host.split(".")
        if all(_looks_numeric_octet(p) for p in parts):
            is_canonical_quad = len(parts) == 4 and all(
                p.isdigit() and not (len(p) > 1 and p[0] == "0") and int(p) <= 255
                for p in parts
            )
            if not is_canonical_quad:
                # 127.1, 0x7f.1, 010.0.0.1, 192.168.257 ... all packed forms.
                return True
    return False


def _looks_numeric_octet(part: str) -> bool:
    """True if ``part`` is a decimal, octal or hexadecimal integer token."""
    if part == "":
        return False
    low = part.lower()
    try:
        if low.startswith("0x"):
            int(low, 16)
            return True
        int(part, 10)
        return True
    except ValueError:
        return False


def _unwrap_embedded_ipv4(
    ip: ipaddress.IPv6Address,
) -> ipaddress.IPv4Address | None:
    """Return the IPv4 address embedded in an IPv6 relay/mapping form, if any.

    Handles IPv4-mapped (``::ffff:a.b.c.d``), NAT64 (``64:ff9b::/96``) and 6to4
    (``2002::/16``). These forms can smuggle a private IPv4 past a naive IPv6
    check, so the embedded v4 is extracted and revalidated by the caller.
    """
    if ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    if ip in _NAT64_PREFIX:
        # Last 32 bits are the IPv4 address.
        return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    if ip in _SIXTOFOUR_PREFIX:
        # Bits 16..47 hold the IPv4 address.
        return ipaddress.IPv4Address((int(ip) >> 80) & 0xFFFFFFFF)
    return None


def _ip_is_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True if an effective (already unwrapped) address is non-routable/forbidden.

    We do not trust ``is_global`` alone; every disqualifying predicate is listed
    explicitly so the policy is auditable and defense-in-depth holds even if a
    stdlib classification changes.
    """
    if (
        ip.is_loopback
        or ip.is_link_local
        or ip.is_private
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
        or not ip.is_global
    ):
        return True
    if isinstance(ip, ipaddress.IPv4Address):
        if ip in _METADATA_V4 or ip in _CGNAT_NET:
            return True
    elif ip in _SITE_LOCAL_V6:
        return True
    return False


def _validate_ip(ip_str: str) -> None:
    """Raise ``BlockedDestination`` if the resolved IP is not a safe public host.

    IPv6 relay/mapping forms are unwrapped to their embedded IPv4 and that
    address is validated instead (both the wrapper and the embedded address must
    pass — the embedded one is the effective destination).
    """
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError as exc:
        raise BlockedDestination(
            "destination is not a public address", details={}
        ) from exc

    effective: ipaddress.IPv4Address | ipaddress.IPv6Address = ip
    if isinstance(ip, ipaddress.IPv6Address):
        embedded = _unwrap_embedded_ipv4(ip)
        if embedded is not None:
            effective = embedded

    if _ip_is_blocked(effective):
        # No IP in the message or details: echoing the resolved address back
        # to the caller is an internal-DNS oracle (audit B1). The host the
        # caller sent is all they learn.
        raise BlockedDestination("destination is not a public address", details={})


def validate_url(
    url: str,
    *,
    resolver: Resolver | None = None,
    settings: Settings | None = None,
) -> GuardResult:
    """Validate ``url`` and return the connect target, or raise a typed error.

    Steps, in order:
      1. Scheme must be http/https (else ``InvalidURL``).
      2. Normalize: lowercase host, strip fragment, IDNA-encode the host.
      3. Resolve the effective port; reject if not in ``settings.allowed_ports``.
      4. Reject IPv4 literals in alternate notation BEFORE resolving.
      5. Resolve ALL A/AAAA records via the injected resolver.
      6. Validate EVERY resolved IP; a single bad one rejects the host. Return
         the first validated IP as the connect target.
    """
    settings = settings or get_settings()
    resolver = resolver or SocketResolver()

    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise InvalidURL(
            f"unsupported scheme: {parts.scheme!r} (only http/https allowed)",
            details={"scheme": parts.scheme},
        )

    raw_host = parts.hostname
    if not raw_host:
        raise InvalidURL("URL has no host", details={"url": url})

    # Bracketed IPv6 literals arrive here already unbracketed from urlsplit.
    is_bracketed_ipv6 = parts.netloc.count(":") > 1 and "[" in parts.netloc

    hostname = raw_host.lower()

    # Strip a single trailing dot (fully-qualified form, e.g. "example.com." or
    # "2130706433."). Without this, a trailing dot would make the pre-resolution
    # alternate-notation literal check skip packed forms like "2130706433." and
    # lean entirely on the resolver (security review LOW-1, 2026-10-11).
    if hostname.endswith(".") and hostname != ".":
        hostname = hostname[:-1]

    # IDNA-encode non-ASCII hostnames (skip IP literals, which are ASCII).
    if not is_bracketed_ipv6 and not _is_ascii(hostname):
        try:
            hostname = hostname.encode("idna").decode("ascii")
        except (UnicodeError, UnicodeDecodeError) as exc:
            raise InvalidURL(
                f"hostname is not valid IDNA: {raw_host!r}",
                details={"host": raw_host},
            ) from exc

    # Port resolution + allow-list.
    try:
        explicit_port = parts.port
    except ValueError as exc:
        raise InvalidURL(
            f"invalid port in URL: {parts.netloc!r}",
            details={"netloc": parts.netloc},
        ) from exc
    port = explicit_port if explicit_port is not None else _DEFAULT_PORTS[scheme]
    if port not in settings.allowed_ports:
        raise BlockedDestination(
            f"port {port} is not in the allow-list {settings.allowed_ports}",
            details={"port": port, "allowed_ports": list(settings.allowed_ports)},
        )

    # Reject alternate-notation IPv4 literals before resolving (IPv6 literals
    # are validated directly, dotted-quad IPv4 proceeds to resolution).
    if not is_bracketed_ipv6 and _is_forbidden_ip_literal(hostname):
        raise InvalidURL(
            f"host is an IP literal in a forbidden notation: {raw_host!r}",
            details={"host": raw_host},
        )

    # If the host is already a usable IP literal (plain dotted-quad or bracketed
    # IPv6), validate it directly without going through the resolver.
    literal_ip = _as_ip_literal(hostname)
    if literal_ip is not None:
        _validate_ip(literal_ip)
        validated_ip = literal_ip
    else:
        ips = resolver.resolve(hostname)
        if not ips:
            raise FetchFailed(
                f"DNS resolution returned no records for {hostname!r}",
                details={"host": hostname},
            )
        # Fail closed: validate every record before accepting any.
        for ip in ips:
            _validate_ip(ip)
        validated_ip = ips[0]

    normalized_url = urlunsplit(
        (scheme, _rebuild_netloc(hostname, explicit_port), parts.path, parts.query, "")
    )
    return GuardResult(
        normalized_url=normalized_url,
        hostname=hostname,
        validated_ip=validated_ip,
        port=port,
    )


def _is_ascii(value: str) -> bool:
    try:
        value.encode("ascii")
        return True
    except UnicodeEncodeError:
        return False


def _as_ip_literal(host: str) -> str | None:
    """Return ``host`` if it is a valid IP literal, else ``None``."""
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        return None


def _rebuild_netloc(hostname: str, explicit_port: int | None) -> str:
    """Rebuild the netloc for the normalized URL (bracket IPv6, keep port)."""
    host_part = f"[{hostname}]" if ":" in hostname else hostname
    if explicit_port is not None:
        return f"{host_part}:{explicit_port}"
    return host_part
