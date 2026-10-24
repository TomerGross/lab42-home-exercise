"""Fixtures shared by every test file.

Each test gets its own SQLite file in pytest's temporary directory, so tests
never share data and never touch services.db.
"""

import pytest

from service_monitor.database import Database
from tests.helpers import sqlite_url


@pytest.fixture
async def database(tmp_path):
    """Return a Database on a fresh SQLite file with its tables created, and close it after the test."""
    database = Database(sqlite_url(tmp_path / "test.db"))
    await database.create_tables()
    yield database
    await database.close()
