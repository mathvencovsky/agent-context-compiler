"""The page date NINA exposes is a calendar date from extracted metadata only."""

from __future__ import annotations

import pytest

from app.extract.ir import Metadata
from app.retrieve.assemble import page_date


def _meta(published: str | None, modified: str | None) -> Metadata:
    return Metadata(
        title=None, lang=None, published_at=published, modified_at=modified, jsonld=[]
    )


@pytest.mark.parametrize(
    ("published", "modified", "expected"),
    [
        (None, None, None),
        ("2024-01-02", None, "2024-01-02"),
        ("2024-01-02", "2025-03-04T10:00:00Z", "2025-03-04"),  # modified first
        ("2024-01-02T23:30:00-05:00", None, "2024-01-02"),  # the page's own day
        ("2024-01-02", "last week", "2024-01-02"),  # unparseable -> next one
        ("2024-13-45", None, None),  # not a calendar date
        ("May 1, 2024", None, None),  # free text is never guessed
        ('2024-01-02" evil="1', None, "2024-01-02"),  # only the date survives
        ("", "", None),
    ],
)
def test_page_date(
    published: str | None, modified: str | None, expected: str | None
) -> None:
    assert page_date(_meta(published, modified)) == expected
