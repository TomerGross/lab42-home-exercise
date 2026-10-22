"""Checks whether a service is reachable. Knows nothing about HTTP or the database."""

import asyncio
import errno
import logging
import socket
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone

from service_monitor.schemas import FailureReason, HealthStatus, ServiceConfig, ServiceStatus

logger = logging.getLogger(__name__)

# Linux/macOS errno values, plus the Windows codes for the same errors (WSAENETUNREACH, WSAEHOSTUNREACH).
UNREACHABLE_ERRNOS = {errno.ENETUNREACH, errno.EHOSTUNREACH, 10051, 10065}


class ServiceChecker(ABC):
    @abstractmethod
    async def check(self, service: ServiceConfig) -> ServiceStatus:
        """Probe the service once and return its status. Must not raise on network failures."""


class TcpServiceChecker(ServiceChecker):
    """A service is UP when a TCP connection to its host and port opens within the timeout."""

    def __init__(self, timeout_seconds: float):
        self.timeout_seconds = timeout_seconds

    async def check(self, service: ServiceConfig) -> ServiceStatus:
        checked_at = datetime.now(timezone.utc).replace(microsecond=0)
        start = time.perf_counter()
        try:
            async with asyncio.timeout(self.timeout_seconds):
                _, writer = await asyncio.open_connection(service.host, service.port)
        except OSError as error:
            logger.info("Check of %s failed: %r", service.name, error)
            return ServiceStatus(
                status=HealthStatus.DOWN,
                last_checked=checked_at,
                failure_reason=_failure_reason(error),
            )
        response_time_ms = round((time.perf_counter() - start) * 1000, 2)
        writer.close()
        return ServiceStatus(
            status=HealthStatus.UP,
            last_checked=checked_at,
            response_time_ms=response_time_ms,
        )


def _failure_reason(error: OSError) -> FailureReason:
    if isinstance(error, TimeoutError):
        return FailureReason.TIMEOUT
    if isinstance(error, ConnectionRefusedError):
        return FailureReason.CONNECTION_REFUSED
    if isinstance(error, socket.gaierror):
        return FailureReason.DNS_ERROR
    if error.errno in UNREACHABLE_ERRNOS:
        return FailureReason.UNREACHABLE
    return FailureReason.OTHER
