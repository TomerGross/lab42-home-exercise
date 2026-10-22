import pytest

from service_monitor.database import Database


def sqlite_url(path) -> str:
    return f"sqlite+aiosqlite:///{path.as_posix()}"


@pytest.fixture
async def database(tmp_path):
    database = Database(sqlite_url(tmp_path / "test.db"))
    await database.create_tables()
    yield database
    await database.close()
