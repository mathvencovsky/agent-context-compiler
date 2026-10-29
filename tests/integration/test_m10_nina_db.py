"""The real Postgres layer of ``prepare_nina_database`` (PREREG Amendment 17).

Runs on a throwaway database name, NEVER on ``bench.m10_tools.NINA_DB``: the
developer's benchmark database must survive the test suite.
"""

from __future__ import annotations

from collections.abc import Iterator

import psycopg
import pytest

from app.config import Settings, get_settings
from bench.m10_tools import NINA_DB, PostgresNinaDatabase, prepare_nina_database

pytestmark = pytest.mark.integration

_NAME = "nina_m10_harness_test"


@pytest.fixture
def db() -> Iterator[PostgresNinaDatabase]:
    assert _NAME != NINA_DB
    database = PostgresNinaDatabase(get_settings(), name=_NAME)
    database.drop()
    yield database
    database.drop()


def _insert_source(settings: Settings) -> None:
    with psycopg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        user=settings.postgres_user,
        password=settings.postgres_password,
        dbname=_NAME,
        autocommit=True,
    ) as conn:
        conn.execute(
            "INSERT INTO sources (id, url_normalized, domain) "
            "VALUES ('doc_x', 'https://docs.example.com/', 'docs.example.com')"
        )


def test_new_run_lifecycle_on_postgres(db: PostgresNinaDatabase) -> None:
    assert db.exists() is False
    assert prepare_nina_database(resuming=False, reset=False, db=db) == "created"
    assert db.exists() is True
    assert db.has_data() is False  # migrated, no rows

    # Run again with nothing stored: an empty database is fine as it is.
    assert prepare_nina_database(resuming=False, reset=False, db=db) == "empty"

    _insert_source(get_settings())
    assert db.has_data() is True
    with pytest.raises(SystemExit, match="--reset-nina-db"):
        prepare_nina_database(resuming=False, reset=False, db=db)
    assert db.has_data() is True  # refused: nothing was dropped

    # Resuming keeps the rows.
    assert prepare_nina_database(resuming=True, reset=False, db=db) == "kept"
    assert db.has_data() is True

    # A new run with the flag starts empty again.
    assert prepare_nina_database(resuming=False, reset=True, db=db) == "reset"
    assert db.exists() is True
    assert db.has_data() is False
