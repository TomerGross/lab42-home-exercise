from datetime import datetime, timezone

import pytest

from service_monitor.errors import DuplicateServiceError, UnknownServiceError
from service_monitor.schemas import HealthStatus, ServiceConfig, ServiceStatus
from service_monitor.service_checker import ServiceChecker
from service_monitor.service_manager import ServiceManager

SERVICE_A = ServiceConfig(name="Service A", host="127.0.0.1", port=8001)
SERVICE_B = ServiceConfig(name="Service B", host="127.0.0.1", port=8002)


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


async def test_duplicate_service_is_rejected(manager):
    await manager.add_services([SERVICE_A])

    with pytest.raises(DuplicateServiceError):
        await manager.add_services([SERVICE_A])


async def test_get_unknown_service_raises(manager):
    with pytest.raises(UnknownServiceError):
        await manager.get_service("missing")


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
