"""Persistence: the services and checks tables, and the Database that reads and writes them.

The services table holds each service's configuration and latest status; the
checks table holds one row per check, the history. This is the only module that
knows SQL.
"""

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, delete, select
from sqlalchemy.engine import Dialect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from service_monitor.errors import DuplicateServiceError, UnknownServiceError
from service_monitor.schemas import (
    MAX_HOSTNAME_LENGTH,
    MAX_SERVICE_NAME_LENGTH,
    CheckType,
    FailureReason,
    HealthStatus,
    Service,
    ServiceConfig,
    ServiceStatus,
)


class Base(DeclarativeBase):
    """The base class of both tables."""


class UtcDateTime(TypeDecorator):
    """A timestamp column that is always read back as UTC.

    SQLite stores datetimes without a timezone, so they come back naive. Every
    timestamp we store is UTC, so it is marked as UTC when read. Databases with
    a timezone column return aware values, which are converted to UTC.
    """

    impl = DateTime(timezone=True)  # a real timezone column on databases that have one, such as PostgreSQL
    cache_ok = True

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        """Return a timestamp read from the database in UTC."""
        if value is None:
            return None
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)


class StatusColumns:
    """The ServiceStatus fields as columns, shared by both tables."""

    status: Mapped[HealthStatus] = mapped_column(default=HealthStatus.UNKNOWN)
    last_checked: Mapped[datetime | None] = mapped_column(UtcDateTime)
    response_time_ms: Mapped[float | None]
    failure_reason: Mapped[FailureReason | None]


class ServiceRecord(StatusColumns, Base):
    """One monitored service and its latest status, as stored in the database."""

    __tablename__ = "services"
    __table_args__ = (UniqueConstraint("host", "port"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(MAX_SERVICE_NAME_LENGTH), unique=True)
    host: Mapped[str] = mapped_column(String(MAX_HOSTNAME_LENGTH))
    port: Mapped[int]
    check_type: Mapped[CheckType] = mapped_column(default=CheckType.TCP)


class CheckRecord(StatusColumns, Base):
    """One past check of a service, as stored in the database."""

    __tablename__ = "checks"

    id: Mapped[int] = mapped_column(primary_key=True)
    service_id: Mapped[int] = mapped_column(ForeignKey("services.id"), index=True)


class Database:
    """Reads and writes services and their checks. Owns the database engine and its connections."""

    def __init__(self, url: str):
        self._engine = create_async_engine(url)
        self._session_factory = async_sessionmaker(self._engine, expire_on_commit=False)

    async def create_tables(self) -> None:
        """Create the tables that don't exist yet. Existing tables are left as they are."""
        async with self._engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def close(self) -> None:
        """Close every database connection."""
        await self._engine.dispose()

    async def add_services(self, configs: list[ServiceConfig]) -> list[Service]:
        """Store the services in one transaction: either all are stored or none are.

        Raises DuplicateServiceError if a name or host:port already exists, or appears twice in `configs`.
        """
        records = [ServiceRecord(**config.model_dump()) for config in configs]
        try:
            async with self._session_factory.begin() as session:
                session.add_all(records)
        except IntegrityError as error:
            raise DuplicateServiceError from error
        return [Service.model_validate(record) for record in records]

    async def list_services(self, names: list[str] | None = None) -> list[Service]:
        """Return every service, or only those named in `names`, sorted by name.

        The names are sent as bound parameters, never as SQL text, so they cannot inject SQL.
        """
        query = select(ServiceRecord).order_by(ServiceRecord.name)
        if names is not None:
            query = query.where(ServiceRecord.name.in_(names))
        async with self._session_factory() as session:
            records = await session.scalars(query)
            return [Service.model_validate(record) for record in records]

    async def get_service(self, name: str) -> Service | None:
        """Return the service with this name, or None if there is none."""
        async with self._session_factory() as session:
            record = await session.scalar(select(ServiceRecord).where(ServiceRecord.name == name))
            return Service.model_validate(record) if record else None

    async def update_status(self, name: str, status: ServiceStatus) -> Service:
        """Store a check result as the service's latest status, and add it to the service's history.

        Both writes are one transaction, committed when the block ends. Raises
        UnknownServiceError if the service no longer exists.
        """
        async with self._session_factory.begin() as session:
            record = await session.scalar(select(ServiceRecord).where(ServiceRecord.name == name))
            if record is None:
                raise UnknownServiceError(name)
            values = status.model_dump()
            for field, value in values.items():
                setattr(record, field, value)  # becomes an UPDATE of the services row on commit
            session.add(CheckRecord(service_id=record.id, **values))
        return Service.model_validate(record)

    async def get_history(self, name: str, limit: int) -> list[ServiceStatus]:
        """Return the service's latest `limit` checks, newest first. An unknown name returns an empty list."""
        query = (
            select(CheckRecord)
            .join(ServiceRecord)
            .where(ServiceRecord.name == name)
            .order_by(CheckRecord.id.desc())
            .limit(limit)
        )
        async with self._session_factory() as session:
            records = await session.scalars(query)
            return [ServiceStatus.model_validate(record) for record in records]

    async def delete_service(self, name: str) -> None:
        """Delete the service and its history in one transaction. Raises UnknownServiceError for an unknown name.

        The checks are deleted explicitly: SQLite ignores foreign keys by default,
        and it can reuse a deleted service's id, which would give its old history to
        a new service.
        """
        async with self._session_factory.begin() as session:
            service_id = await session.scalar(select(ServiceRecord.id).where(ServiceRecord.name == name))
            if service_id is None:
                raise UnknownServiceError(name)
            await session.execute(delete(CheckRecord).where(CheckRecord.service_id == service_id))
            await session.execute(delete(ServiceRecord).where(ServiceRecord.id == service_id))
