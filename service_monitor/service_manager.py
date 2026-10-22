"""Business logic for every operation the API offers, plus background monitoring. Knows nothing about HTTP or SQL."""

import asyncio
import logging

from service_monitor.database import Database
from service_monitor.errors import UnknownServiceError
from service_monitor.schemas import Service, ServiceConfig
from service_monitor.service_checker import ServiceChecker

logger = logging.getLogger(__name__)

MAX_CONCURRENT_CHECKS = 100  # caps open sockets, so a long service list cannot exhaust the OS file-descriptor limit


class ServiceManager:
    def __init__(
        self, database: Database, checker: ServiceChecker, max_concurrent_checks: int = MAX_CONCURRENT_CHECKS
    ):
        self._database = database
        self._checker = checker
        self._check_slots = asyncio.Semaphore(max_concurrent_checks)

    async def add_services(self, configs: list[ServiceConfig]) -> list[Service]:
        return await self._database.add_services(configs)

    async def list_services(self, names: list[str] | None = None) -> list[Service]:
        """All services, or only the named ones. Fails on the first name that does not exist."""
        services = await self._database.list_services(names)
        if names is not None:
            found = {service.name for service in services}
            for name in names:
                if name not in found:
                    raise UnknownServiceError(name)
        return services

    async def get_service(self, name: str) -> Service:
        service = await self._database.get_service(name)
        if service is None:
            raise UnknownServiceError(name)
        return service

    async def check_service(self, name: str) -> Service:
        """Probe the service now and store the result as its latest status."""
        service = await self.get_service(name)
        return await self._check_and_store(service)

    async def check_services(self, names: list[str] | None = None) -> list[Service]:
        """Probe the named services (every service when no names are given) concurrently and store each result."""
        services = await self.list_services(names)
        return await asyncio.gather(*(self._check_and_store(service) for service in services))

    async def monitor_services(self, interval_seconds: float) -> None:
        """Check every service every interval_seconds, until cancelled."""
        while True:
            await asyncio.sleep(interval_seconds)
            try:
                await self.check_services()
            except Exception:
                logger.exception("Monitoring round failed")

    async def delete_service(self, name: str) -> None:
        await self._database.delete_service(name)

    async def _check_and_store(self, service: Service) -> Service:
        """Probe one service and store the result. Waits for a free slot when the concurrency limit is reached."""
        async with self._check_slots:
            status = await self._checker.check(service)
        return await self._database.update_status(service.name, status)
