import asyncio
from datetime import datetime, timezone

import pytest

from service_monitor.errors import DuplicateServiceError, UnknownServiceError
from service_monitor.schemas import HealthStatus, ServiceConfig, ServiceStatus
from service_monitor.service_checker import ServiceChecker
from service_monitor.service_manager import ServiceManager

SERVICE_A = ServiceConfig(name="Service A", host="127.0.0.1", port=8001)
SERVICE_B = ServiceConfig(name="Service B", host="127.0.0.1", port=8002)
SERVICE_C = ServiceConfig(name="Service C", host="127.0.0.1", port=8003)


class FakeChecker(ServiceChecker):
    """Reports every service as UP without touching the network."""

    def __init__(self):
        self.checked_names = []

    async def check(self, service: ServiceConfig) -> ServiceStatus:
        self.checked_names.append(service.name)
        return ServiceStatus(
            status=HealthStatus.UP,
            last_checked=datetime(2026, 10, 8, 10, 30, tzinfo=timezone.utc),
            response_time_ms=5.0,
        )


@pytest.fixture
def checker():
    return FakeChecker()


@pytest.fixture
def manager(database, checker):
    return ServiceManager(database, checker)


async def test_added_services_can_be_listed_and_fetched(manager):
    await manager.add_services([SERVICE_B, SERVICE_A])

    assert [service.name for service in await manager.list_services()] == ["Service A", "Service B"]
    assert (await manager.get_service("Service B")).port == 8002


async def test_list_services_by_names_returns_only_those(manager):
    await manager.add_services([SERVICE_A, SERVICE_B, SERVICE_C])

    services = await manager.list_services(["Service C", "Service A"])

    assert [service.name for service in services] == ["Service A", "Service C"]


async def test_list_services_with_unknown_name_raises(manager):
    await manager.add_services([SERVICE_A])

    with pytest.raises(UnknownServiceError, match="missing"):
        await manager.list_services(["Service A", "missing"])


async def test_duplicate_service_is_rejected(manager):
    await manager.add_services([SERVICE_A])

    with pytest.raises(DuplicateServiceError):
        await manager.add_services([SERVICE_A])


async def test_get_unknown_service_raises(manager):
    with pytest.raises(UnknownServiceError):
        await manager.get_service("missing")


async def test_delete_service_removes_it(manager):
    await manager.add_services([SERVICE_A])

    await manager.delete_service("Service A")

    with pytest.raises(UnknownServiceError):
        await manager.get_service("Service A")


async def test_check_service_stores_and_returns_the_new_status(manager, checker):
    await manager.add_services([SERVICE_A])

    checked = await manager.check_service("Service A")

    assert checker.checked_names == ["Service A"]
    assert checked.status == HealthStatus.UP
    assert checked.response_time_ms == 5.0
    assert (await manager.get_service("Service A")).status == HealthStatus.UP


async def test_check_unknown_service_raises_without_probing(manager, checker):
    with pytest.raises(UnknownServiceError):
        await manager.check_service("missing")

    assert checker.checked_names == []


async def test_check_services_checks_only_the_named_services(manager, checker):
    await manager.add_services([SERVICE_A, SERVICE_B, SERVICE_C])

    checked = await manager.check_services(["Service C", "Service A"])

    assert [service.name for service in checked] == ["Service A", "Service C"]
    assert sorted(checker.checked_names) == ["Service A", "Service C"]
    assert (await manager.get_service("Service B")).status == HealthStatus.UNKNOWN


async def test_check_services_without_names_checks_every_service(manager, checker):
    await manager.add_services([SERVICE_A, SERVICE_B])

    checked = await manager.check_services()

    assert [service.status for service in checked] == [HealthStatus.UP, HealthStatus.UP]
    assert sorted(checker.checked_names) == ["Service A", "Service B"]


async def test_check_services_with_unknown_name_raises_without_probing(manager, checker):
    await manager.add_services([SERVICE_A])

    with pytest.raises(UnknownServiceError, match="missing"):
        await manager.check_services(["Service A", "missing"])

    assert checker.checked_names == []


class CountingChecker(FakeChecker):
    """Records the highest number of checks that were running at the same time."""

    def __init__(self):
        super().__init__()
        self.running = 0
        self.max_running = 0

    async def check(self, service: ServiceConfig) -> ServiceStatus:
        self.running += 1
        self.max_running = max(self.max_running, self.running)
        await asyncio.sleep(0.01)
        self.running -= 1
        return await super().check(service)


async def test_check_services_runs_checks_concurrently_up_to_the_limit(database):
    checker = CountingChecker()
    manager = ServiceManager(database, checker, max_concurrent_checks=2)
    await manager.add_services([ServiceConfig(name=f"S{port}", host="127.0.0.1", port=port) for port in range(1, 6)])

    await manager.check_services()

    assert checker.max_running == 2
    assert len(checker.checked_names) == 5
