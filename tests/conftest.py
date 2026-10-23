import pytest

from service_monitor.database import Database
from tests.helpers import sqlite_url


@pytest.fixture
async def database(tmp_path):
    database = Database(sqlite_url(tmp_path / "test.db"))
    await database.create_tables()
    yield database
    await database.close()
