"""SSRF guard: parametrized blocklist, positives, and edge cases (SPEC §3.1).

Every case injects a FakeResolver, so no DNS is ever performed and the
no-network fixture stays satisfied. Alternate-notation literals must be rejected
BEFORE the resolver is consulted; those cases pass an empty resolver and the
test also asserts the resolver was never called.
"""

from __future__ import annotations

import pytest

from app.config import Settings
from app.fetch.errors import BlockedDestination, FetchFailed, InvalidURL
from app.fetch.guard import validate_url
from tests.unit.fetch._resolvers import FakeResolver

SETTINGS = Settings()

# Resolver used for name-based hosts in the blocklist. Alternate-notation
# literals are rejected pre-resolution and never look anything up.
_RESOLVER = FakeResolver(
    {
        "localhost": ["127.0.0.1"],
        "evil.example.com": ["10.0.0.1"],
        "public.example.com": ["93.184.216.34"],
    }
)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        # Loopback / localhost (IPv4, IPv6, name).
        ("http://127.0.0.1", BlockedDestination),
        ("http://localhost", BlockedDestination),
        ("http://[::1]", BlockedDestination),
        # Cloud metadata + private ranges.
        ("http://169.254.169.254/latest/meta-data", BlockedDestination),
        ("http://10.0.0.1", BlockedDestination),
        # Alternate-notation IPv4 literals -> invalid_url (pre-resolution).
        ("http://2130706433", InvalidURL),
        ("http://0x7f000001", InvalidURL),
        # Same packed literals with a trailing dot must still be rejected
        # pre-resolution (security review LOW-1): the trailing dot is stripped
        # during normalization so the literal check is not bypassed.
        ("http://2130706433.", InvalidURL),
        ("http://0x7f000001.", InvalidURL),
        # IPv4-mapped IPv6 loopback -> extract embedded v4 -> blocked.
        ("http://[::ffff:127.0.0.1]", BlockedDestination),
        # Forbidden schemes.
        ("file:///etc/passwd", InvalidURL),
        ("gopher://example.com/", InvalidURL),
        # Disallowed port.
        ("http://public.example.com:8080", BlockedDestination),
        # CGNAT and link-local literals.
        ("http://100.64.0.1", BlockedDestination),
        ("http://169.254.1.1", BlockedDestination),
    ],
)
def test_blocklist(url: str, expected: type[Exception]) -> None:
    with pytest.raises(expected):
        validate_url(url, resolver=_RESOLVER, settings=SETTINGS)


@pytest.mark.parametrize(
    "url",
    [
        "http://2130706433",
        "http://0x7f000001",
        "http://017700000001",  # octal packed form
        "http://127.1",  # short/packed dotted form
        "http://2130706433.",  # packed decimal with trailing dot (LOW-1)
        "http://0x7f000001.",  # packed hex with trailing dot (LOW-1)
    ],
)
def test_alternate_notation_rejected_before_resolving(url: str) -> None:
    """These never reach the resolver — assert zero resolver calls."""
    resolver = FakeResolver({})
    with pytest.raises(InvalidURL):
        validate_url(url, resolver=resolver, settings=SETTINGS)
    assert resolver.calls == []


def test_localhost_blocked_via_resolver() -> None:
    """`localhost` is name-resolved by the fake to 127.0.0.1, then rejected."""
    resolver = FakeResolver({"localhost": ["127.0.0.1"]})
    with pytest.raises(BlockedDestination):
        validate_url("http://localhost/x", resolver=resolver, settings=SETTINGS)
    assert resolver.calls == ["localhost"]


def test_mixed_ip_set_fails_closed() -> None:
    """One bad record among good ones rejects the whole host."""
    resolver = FakeResolver({"mixed.example.com": ["93.184.216.34", "10.0.0.5"]})
    with pytest.raises(BlockedDestination):
        validate_url("http://mixed.example.com", resolver=resolver, settings=SETTINGS)


def test_empty_resolution_is_fetch_failed() -> None:
    resolver = FakeResolver({"nxdomain.example.com": []})
    with pytest.raises(FetchFailed):
        validate_url(
            "http://nxdomain.example.com", resolver=resolver, settings=SETTINGS
        )


def test_public_ipv4_name_ok() -> None:
    resolver = FakeResolver({"public.example.com": ["93.184.216.34"]})
    result = validate_url(
        "http://public.example.com/docs", resolver=resolver, settings=SETTINGS
    )
    assert result.hostname == "public.example.com"
    assert result.validated_ip == "93.184.216.34"
    assert result.port == 80
    assert result.normalized_url == "http://public.example.com/docs"


def test_public_ipv6_name_ok() -> None:
    resolver = FakeResolver({"v6.example.com": ["2606:2800:220:1:248:1893:25c8:1946"]})
    result = validate_url(
        "https://v6.example.com", resolver=resolver, settings=SETTINGS
    )
    assert result.validated_ip == "2606:2800:220:1:248:1893:25c8:1946"
    assert result.port == 443


def test_public_ipv4_literal_ok() -> None:
    """A plain dotted-quad public literal is allowed and not re-resolved."""
    resolver = FakeResolver({})
    result = validate_url(
        "http://93.184.216.34/x", resolver=resolver, settings=SETTINGS
    )
    assert result.validated_ip == "93.184.216.34"
    assert resolver.calls == []


def test_host_lowercased_and_fragment_stripped() -> None:
    resolver = FakeResolver({"public.example.com": ["93.184.216.34"]})
    result = validate_url(
        "http://PUBLIC.Example.COM/Path#frag", resolver=resolver, settings=SETTINGS
    )
    assert result.hostname == "public.example.com"
    assert "#" not in result.normalized_url
    assert result.normalized_url == "http://public.example.com/Path"


def test_tracking_params_removed_from_normalized_url() -> None:
    """Tracking parameters never change the page, so they never change its id."""
    resolver = FakeResolver({"public.example.com": ["93.184.216.34"]})
    result = validate_url(
        "http://public.example.com/p?utm_source=x&a=1&UTM_Medium=y&authuser=8"
        "&hl=it&b=%20&gclid=z&utm_campaign",
        resolver=resolver,
        settings=SETTINGS,
    )
    assert result.normalized_url == "http://public.example.com/p?a=1&hl=it&b=%20"


def test_only_tracking_params_leaves_no_query() -> None:
    resolver = FakeResolver({"public.example.com": ["93.184.216.34"]})
    plain = validate_url(
        "http://public.example.com/p", resolver=resolver, settings=SETTINGS
    )
    tracked = validate_url(
        "http://public.example.com/p?fbclid=1", resolver=resolver, settings=SETTINGS
    )
    assert (
        tracked.normalized_url == plain.normalized_url == "http://public.example.com/p"
    )


def test_trailing_dot_fqdn_normalized() -> None:
    """A fully-qualified name with a trailing dot resolves under its bare form."""
    resolver = FakeResolver({"public.example.com": ["93.184.216.34"]})
    result = validate_url(
        "http://public.example.com./x", resolver=resolver, settings=SETTINGS
    )
    assert result.hostname == "public.example.com"
    assert resolver.calls == ["public.example.com"]
    assert result.normalized_url == "http://public.example.com/x"


def test_configurable_port_allowed() -> None:
    resolver = FakeResolver({"public.example.com": ["93.184.216.34"]})
    settings = Settings(allowed_ports=(80, 443, 8443))
    result = validate_url(
        "https://public.example.com:8443", resolver=resolver, settings=settings
    )
    assert result.port == 8443
