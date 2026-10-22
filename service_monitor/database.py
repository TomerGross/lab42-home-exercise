"""Persistence: the services table and the Database that reads and writes it. The only module that knows SQL."""

from datetime import datetime, timezone

from sqlalchemy import String, UniqueConstraint, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from service_monitor.errors import DuplicateServiceError, UnknownServiceError
from service_monitor.schemas import (
    MAX_HOSTNAME_LENGTH,
    MAX_SERVICE_NAME_LENGTH,
    FailureReason,
    HealthStatus,
    Service,
    ServiceConfig,
    ServiceStatus,
)


class Base(DeclarativeBase):
    pass


class ServiceRecord(Base):
    """One monitored service as stored in the database."""

    __tablename__ = "services"
    __table_args__ = (UniqueConstraint("host", "port"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(MAX_SERVICE_NAME_LENGTH), unique=True)
    host: Mapped[str] = mapped_column(String(MAX_HOSTNAME_LENGTH))
    port: Mapped[int]
    status: Mapped[HealthStatus] = mapped_column(default=HealthStatus.UNKNOWN)
    last_checked: Mapped[datetime | None]
    response_time_ms: Mapped[float | None]
    failure_reason: Mapped[FailureReason | None]


class Database:
    def __init__(self, url: str):
        self._engine = create_async_engine(url)
        self._session_factory = async_sessionmaker(self._engine, expire_on_commit=False)

    async def create_tables(self) -> None:
        async with self._engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def close(self) -> None:
        await self._engine.dispose()

    async def add_services(self, configs: list[ServiceConfig]) -> list[Service]:
        """Save all services in one transaction: either all are stored or none are."""
        records = [ServiceRecord(**config.model_dump()) for config in configs]
        try:
            async with self._session_factory.begin() as session:
                session.add_all(records)
        except IntegrityError as error:
            raise DuplicateServiceError("a service with this name or host:port already exists") from error
        return [_to_service(record) for record in records]

    async def list_services(self) -> list[Service]:
        async with self._session_factory() as session:
            records = await session.scalars(select(ServiceRecord).order_by(ServiceRecord.name))
            return [_to_service(record) for record in records]

    async def get_service(self, name: str) -> Service | None:
        async with self._session_factory() as session:
            record = await session.scalar(select(ServiceRecord).where(ServiceRecord.name == name))
            return _to_service(record) if record else None

    async def update_status(self, name: str, status: ServiceStatus) -> Service:
        """Store the result of a check as the service's latest status."""
        async with self._session_factory.begin() as session:
            record = await session.scalar(select(ServiceRecord).where(ServiceRecord.name == name))
            if record is None:
                raise UnknownServiceError(name)
            for field, value in status.model_dump().items():
                setattr(record, field, value)
        return _to_service(record)


def _to_service(record: ServiceRecord) -> Service:
    service = Service.model_validate(record, from_attributes=True)
    if service.last_checked is not None:
        # SQLite drops timezone info; every timestamp we store is UTC.
        service.last_checked = service.last_checked.replace(tzinfo=timezone.utc)
    return service
