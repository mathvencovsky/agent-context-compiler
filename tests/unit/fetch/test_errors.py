"""Each fetch error maps 1:1 to its SPEC §8 (code, http_status) pair."""

from __future__ import annotations

import pytest

from app.fetch import errors

# (class, code, http_status) straight from the SPEC §8 table.
_CASES = [
    (errors.InvalidURL, "invalid_url", 400),
    (errors.BlockedDestination, "blocked_destination", 400),
    (errors.RobotsDisallowed, "robots_disallowed", 403),
    (errors.RobotsUnavailable, "robots_unavailable", 503),
    (errors.TooLarge, "too_large", 413),
    (errors.UnsupportedContentType, "unsupported_content_type", 415),
    (errors.FetchFailed, "fetch_failed", 502),
    (errors.Timeout, "timeout", 504),
]


@pytest.mark.parametrize(("cls", "code", "status"), _CASES)
def test_error_code_and_status(
    cls: type[errors.FetchError], code: str, status: int
) -> None:
    assert cls.code == code
    assert cls.http_status == status


@pytest.mark.parametrize(("cls", "code", "status"), _CASES)
def test_errors_share_base_and_carry_payload(
    cls: type[errors.FetchError], code: str, status: int
) -> None:
    err = cls("boom", details={"k": "v"})
    assert isinstance(err, errors.FetchError)
    assert isinstance(err, Exception)
    assert err.message == "boom"
    assert err.details == {"k": "v"}
    assert str(err) == "boom"


def test_details_default_is_empty_dict() -> None:
    err = errors.FetchFailed("x")
    assert err.details == {}


def test_refusal_note_names_block_and_alternative() -> None:
    for status in (401, 403):
        note = errors.refusal_note(status)
        assert note is not None
        assert "refused" in note and "another way" in note
    limited = errors.refusal_note(429)
    assert limited is not None
    assert "rate-limited" in limited and "another way" in limited
    assert errors.refusal_note(404) is None
