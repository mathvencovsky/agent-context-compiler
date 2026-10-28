"""Deterministic resolver fakes for the SSRF tests (no DNS, no network).

``FakeResolver`` maps hostnames to fixed IP lists. ``SequenceResolver`` returns
a different answer on each successive call for the same host — used by the
DNS-rebinding test to return a public IP at validation time and a private one
afterwards, proving the client connects to the first (validated) IP and never
re-resolves.
"""

from __future__ import annotations

from typing import Any


class FakeResolver:
    """Resolver returning a fixed IP list per host (implements guard.Resolver)."""

    def __init__(self, mapping: dict[str, list[str]]) -> None:
        self._mapping = mapping
        self.calls: list[str] = []

    def resolve(self, host: str) -> list[str]:
        self.calls.append(host)
        return list(self._mapping.get(host, []))


class SequenceResolver:
    """Resolver returning queued answers in order, repeating the last one.

    Each entry is the full list returned by one ``resolve`` call. Once the queue
    is exhausted the final entry is returned for all further calls. ``calls``
    records every queried host so a test can assert exactly one resolution.
    """

    def __init__(self, answers: list[list[str]]) -> None:
        if not answers:
            raise ValueError("SequenceResolver needs at least one answer")
        self._answers = answers
        self.calls: list[str] = []

    def resolve(self, host: str) -> list[str]:
        index = min(len(self.calls), len(self._answers) - 1)
        self.calls.append(host)
        return list(self._answers[index])


def loopback_connector(mappings: tuple[str, ...] | list[str]) -> Any:
    """Egress-proxy connector mapping validated hostnames to loopback ports.

    ``mappings`` are Chromium-style ``"MAP <host> 127.0.0.1:<port>"`` rules. The
    proxy calls the connector only AFTER the guard accepted the destination, so
    a blocked host listed here is never connected to: the mapping only exists so
    that a guard bypass would be COUNTED by the fixture instead of escaping.
    A validated host with no mapping raises (tests never touch the internet).
    """
    import socket

    table: dict[str, int] = {}
    for rule in mappings:
        _map, host, target = rule.split()
        table[host] = int(target.rsplit(":", 1)[1])

    def connect(guard: Any, timeout: float) -> socket.socket:
        port = table.get(guard.hostname)
        if port is None:
            raise OSError(f"no loopback mapping for {guard.hostname}")
        return socket.create_connection(("127.0.0.1", port), timeout=timeout)

    return connect
