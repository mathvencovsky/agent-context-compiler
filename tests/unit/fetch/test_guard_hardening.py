"""Guard hardening from the 2026-10-28 audit and security research.

* ``fec0::/10`` (deprecated IPv6 site-local) and Azure's ``168.63.129.16``
  were ACCEPTED (stdlib ``is_global`` is True for both).
* Audit B1: a refusal echoed the resolved internal IP back to the caller
  (an internal-DNS oracle). The message and details must not carry it.
"""

from __future__ import annotations

import pytest

from app.config import Settings
from app.fetch.errors import BlockedDestination
from app.fetch.guard import validate_url
from tests.unit.fetch._resolvers import FakeResolver

SETTINGS = Settings()


@pytest.mark.parametrize(
    "url",
    [
        "http://[fec0::1]/",
        "http://[feff:ffff::1]/",
        "http://168.63.129.16/",
        "http://[::ffff:168.63.129.16]/",
        "http://[::127.0.0.1]/",
        "http://[::169.254.169.254]/",
    ],
)
def test_literal_destinations_are_blocked(url: str) -> None:
    with pytest.raises(BlockedDestination):
        validate_url(url, resolver=FakeResolver({}), settings=SETTINGS)


@pytest.mark.parametrize("ip", ["fec0::1", "168.63.129.16"])
def test_resolved_destinations_are_blocked(ip: str) -> None:
    resolver = FakeResolver({"sneaky.example.com": [ip]})
    with pytest.raises(BlockedDestination):
        validate_url("http://sneaky.example.com/", resolver=resolver, settings=SETTINGS)


def test_public_addresses_still_pass() -> None:
    for ip in ("93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946", "168.63.129.17"):
        resolver = FakeResolver({"docs.example.com": [ip]})
        assert (
            validate_url(
                "https://docs.example.com/", resolver=resolver, settings=SETTINGS
            ).validated_ip
            == ip
        )


def test_refusal_does_not_echo_the_resolved_ip() -> None:
    resolver = FakeResolver({"db.internal.example.com": ["10.20.30.40"]})
    with pytest.raises(BlockedDestination) as excinfo:
        validate_url(
            "http://db.internal.example.com/", resolver=resolver, settings=SETTINGS
        )
    err = excinfo.value
    assert "10.20.30.40" not in err.message
    assert "10.20.30.40" not in repr(err.details)
