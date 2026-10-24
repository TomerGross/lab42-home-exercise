"""Business logic for every operation the API offers, plus the monitoring loop.

Knows nothing about HTTP or SQL. The database and the checker are passed in, so
tests can replace either one.
"""

import asyncio
import logging

from service_monitor.database import Database
from service_monitor.errors import UnknownServiceError
from service_monitor.schemas import Service, ServiceConfig, ServiceStatus
from service_monitor.service_checker import ServiceChecker

logger = logging.getLogger(__name__)

MAX_CONCURRENT_CHECKS = 100  # max open sockets, so a long service list cannot exhaust the OS file-descriptor limit


class ServiceManager:
    """Adds, lists, checks, monitors and deletes services, using the database and the checker."""

    def __init__(self, database: Database, checker: ServiceChecker, max_concurrent_checks: int = MAX_CONCURRENT_CHECKS):
        self._database = database
        self._checker = checker
        self._check_slots = asyncio.Semaphore(max_concurrent_checks)

    async def add_services(self, configs: list[ServiceConfig]) -> list[Service]:
        """Store new services, all or none. Raises DuplicateServiceError for a duplicate name or host:port."""
        return await self._database.add_services(configs)

    async def list_services(self, names: list[str] | None = None) -> list[Service]:
        """Return every service, or only the named ones, sorted by name.

        Raises UnknownServiceError for the first name that does not exist.
        """
        services = await self._database.list_services(names)
        if names is not None:
            found = {service.name for service in services}
            for name in names:
                if name not in found:
                    raise UnknownServiceError(name)
        return services

    async def get_service(self, name: str) -> Service:
        """Return the service with this name. Raises UnknownServiceError if there is none."""
        service = await self._database.get_service(name)
        if service is None:
            raise UnknownServiceError(name)
        return service

    async def check_service(self, name: str) -> Service:
        """Check the service now, store the result and return the updated service.

        Raises UnknownServiceError for an unknown name, without checking anything.
        """
        service = await self.get_service(name)
        return await self._check_and_store(service)

    async def check_services(self, names: list[str] | None = None) -> list[Service]:
        """Check the named services, or every service when no names are given, concurrently.

        Stores each result and returns the updated services, sorted by name.
        Raises UnknownServiceError before checking anything if a name does not
        exist.
        """
        services = await self.list_services(names)
        return await asyncio.gather(*(self._check_and_store(service) for service in services))

    async def get_history(self, name: str, limit: int) -> list[ServiceStatus]:
        """Return the service's latest `limit` checks, newest first. Raises UnknownServiceError for an unknown name."""
        await self.get_service(name)  # the database alone would return an empty history for an unknown name
        return await self._database.get_history(name, limit)

    async def delete_service(self, name: str) -> None:
        """Delete the service and its history. Raises UnknownServiceError for an unknown name."""
        await self._database.delete_service(name)

    async def monitor_services(self, interval_seconds: float) -> None:
        """Check every service every `interval_seconds`, until cancelled.

        The first round runs one interval after the start. A failed round is
        logged, and the next one runs as usual.
        """
        while True:
            await asyncio.sleep(interval_seconds)
            try:
                await self.check_services()
            except Exception:
                logger.exception("Monitoring round failed")

    async def _check_and_store(self, service: Service) -> Service:
        """Check one service and store the result. Waits for a free slot when the concurrency limit is reached."""
        async with self._check_slots:
            status = await self._checker.check(service)
        return await self._database.update_status(service.name, status)
