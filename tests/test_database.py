from datetime import datetime, timezone

import pytest

from service_monitor.database import Database
from service_monitor.errors import DuplicateServiceError, UnknownServiceError
from service_monitor.schemas import FailureReason, HealthStatus, ServiceConfig, ServiceStatus
from tests.conftest import sqlite_url

SERVICE_A = ServiceConfig(name="Service A", host="127.0.0.1", port=8001)
SERVICE_B = ServiceConfig(name="Service B", host="127.0.0.1", port=8002)

conflicts_with_service_a = pytest.mark.parametrize(
    "duplicate",
    [
        ServiceConfig(name="Service A", host="10.0.0.1", port=1),
        ServiceConfig(name="Other", host="127.0.0.1", port=8001),
    ],
    ids=["same-name", "same-address"],
)


async def test_list_services_is_sorted_by_name(database):
    await database.add_services([SERVICE_B, SERVICE_A])

    names = [service.name for service in await database.list_services()]

    assert names == ["Service A", "Service B"]


async def test_list_services_filtered_by_names(database):
    await database.add_services([SERVICE_A, SERVICE_B])

    names = [service.name for service in await database.list_services(["Service B", "missing"])]

    assert names == ["Service B"]


@pytest.mark.parametrize("injection", ["' OR '1'='1", "Service A'); DROP TABLE services; --"])
async def test_names_are_never_executed_as_sql(database, injection):
    await database.add_services([SERVICE_A, ServiceConfig(name=injection, host="10.0.0.1", port=1)])

    assert [service.name for service in await database.list_services([injection])] == [injection]
    assert (await database.get_service(injection)).name == injection
    assert len(await database.list_services()) == 2


async def test_added_service_starts_unknown(database):
    [saved] = await database.add_services([SERVICE_A])

    assert saved.name == "Service A"
    assert saved.status == HealthStatus.UNKNOWN
    assert saved.last_checked is None


async def test_get_service(database):
    await database.add_services([SERVICE_A])

    assert (await database.get_service("Service A")).port == 8001
    assert await database.get_service("missing") is None


@conflicts_with_service_a
async def test_duplicates_are_rejected_and_nothing_is_saved(database, duplicate):
    await database.add_services([SERVICE_A])

    with pytest.raises(DuplicateServiceError):
        await database.add_services([SERVICE_B, duplicate])

    assert [service.name for service in await database.list_services()] == ["Service A"]


@conflicts_with_service_a
async def test_duplicates_within_one_batch_are_rejected(database, duplicate):
    with pytest.raises(DuplicateServiceError):
        await database.add_services([SERVICE_A, duplicate])

    assert await database.list_services() == []


async def test_update_status_round_trips_in_utc(database):
    await database.add_services([SERVICE_A])
    checked_at = datetime(2026, 10, 8, 10, 30, tzinfo=timezone.utc)
    status = ServiceStatus(
        status=HealthStatus.DOWN, last_checked=checked_at, failure_reason=FailureReason.TIMEOUT
    )

    await database.update_status("Service A", status)
    stored = await database.get_service("Service A")

    assert stored.status == HealthStatus.DOWN
    assert stored.failure_reason == FailureReason.TIMEOUT
    assert stored.last_checked == checked_at
    assert stored.last_checked.tzinfo is not None


async def test_update_status_of_unknown_service_raises(database):
    status = ServiceStatus(status=HealthStatus.UP, last_checked=datetime.now(timezone.utc))

    with pytest.raises(UnknownServiceError):
        await database.update_status("missing", status)


def make_status(health: HealthStatus, minute: int) -> ServiceStatus:
    return ServiceStatus(status=health, last_checked=datetime(2026, 10, 8, 10, minute, tzinfo=timezone.utc))


async def test_update_status_adds_to_history_newest_first(database):
    await database.add_services([SERVICE_A])

    await database.update_status("Service A", make_status(HealthStatus.DOWN, minute=1))
    await database.update_status("Service A", make_status(HealthStatus.UP, minute=2))
    history = await database.get_history("Service A", limit=10)

    assert [status.status for status in history] == [HealthStatus.UP, HealthStatus.DOWN]
    assert history[0].last_checked == datetime(2026, 10, 8, 10, 2, tzinfo=timezone.utc)


async def test_get_history_returns_at_most_limit_entries(database):
    await database.add_services([SERVICE_A])
    for minute in range(3):
        await database.update_status("Service A", make_status(HealthStatus.UP, minute))

    assert len(await database.get_history("Service A", limit=2)) == 2


async def test_get_history_of_unchecked_service_is_empty(database):
    await database.add_services([SERVICE_A])

    assert await database.get_history("Service A", limit=10) == []


async def test_get_history_of_unknown_service_is_empty(database):
    assert await database.get_history("missing", limit=10) == []


async def test_deleting_a_service_deletes_its_history(database):
    await database.add_services([SERVICE_A])
    await database.update_status("Service A", make_status(HealthStatus.UP, minute=1))

    await database.delete_service("Service A")
    await database.add_services([SERVICE_B])  # SQLite reuses the deleted row's id for the new service

    assert await database.get_history("Service B", limit=10) == []


async def test_delete_service_removes_only_that_service(database):
    await database.add_services([SERVICE_A, SERVICE_B])

    await database.delete_service("Service A")

    assert [service.name for service in await database.list_services()] == ["Service B"]


async def test_delete_unknown_service_raises(database):
    with pytest.raises(UnknownServiceError):
        await database.delete_service("missing")


async def test_deleted_service_can_be_added_again(database):
    await database.add_services([SERVICE_A])
    await database.delete_service("Service A")

    [saved] = await database.add_services([SERVICE_A])

    assert saved.name == "Service A"


async def test_data_survives_a_restart(tmp_path):
    url = sqlite_url(tmp_path / "restart.db")
    first = Database(url)
    await first.create_tables()
    await first.add_services([SERVICE_A])
    await first.close()

    second = Database(url)
    await second.create_tables()
    restored = await second.get_service("Service A")
    await second.close()

    assert restored is not None
