"""Business logic for the assignment's four operations (§2.1–2.4)."""

from service_monitor.database import Database
from service_monitor.errors import UnknownServiceError
from service_monitor.schemas import Service, ServiceConfig
from service_monitor.service_checker import ServiceChecker


class ServiceManager:
    def __init__(self, database: Database, checker: ServiceChecker):
        self._database = database
        self._checker = checker

    async def add_services(self, configs: list[ServiceConfig]) -> list[Service]:
        return await self._database.add_services(configs)

    async def list_services(self) -> list[Service]:
        return await self._database.list_services()

    async def get_service(self, name: str) -> Service:
        service = await self._database.get_service(name)
        if service is None:
            raise UnknownServiceError(name)
        return service

    async def check_service(self, name: str) -> Service:
        """Probe the service now and store the result as its latest status."""
        service = await self.get_service(name)
        status = await self._checker.check(service)
        return await self._database.update_status(name, status)
