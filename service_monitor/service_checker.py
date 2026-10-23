"""Checks whether a service is reachable. Knows nothing about the API or the database."""

import asyncio
import errno
import logging
import socket
import time
from abc import ABC, abstractmethod
from datetime import UTC, datetime

import httpx2

from service_monitor.schemas import CheckType, FailureReason, HealthStatus, ServiceConfig, ServiceStatus

logger = logging.getLogger(__name__)

UNREACHABLE_ERRNOS = {errno.ENETUNREACH, errno.EHOSTUNREACH}
SSH_BANNER_PREFIX = b"SSH-"  # every SSH server greets with "SSH-<version>-..." (RFC 4253 §4.2)


class UnexpectedResponseError(Exception):
    """The service accepted the connection but did not answer like its check type should."""


class ServiceChecker(ABC):
    @abstractmethod
    async def check(self, service: ServiceConfig) -> ServiceStatus:
        """Probe the service once and return its status. Must not raise on network failures."""


class NetworkServiceChecker(ServiceChecker):
    """A service is UP when it answers its check type within the timeout:
    tcp opens a connection, ssh sends an SSH banner, http/https answers GET / without a 5xx status."""

    def __init__(self, timeout_seconds: float):
        self.timeout_seconds = timeout_seconds

    async def check(self, service: ServiceConfig) -> ServiceStatus:
        checked_at = datetime.now(UTC).replace(microsecond=0)
        start = time.perf_counter()
        try:
            async with asyncio.timeout(self.timeout_seconds):
                match service.check_type:
                    case CheckType.TCP:
                        await _check_tcp(service)
                    case CheckType.SSH:
                        await _check_ssh(service)
                    case CheckType.HTTP | CheckType.HTTPS:
                        await _check_http(service)
        except (OSError, httpx2.TransportError, UnexpectedResponseError) as error:
            logger.info("Check of %s failed: %r", service.name, error)
            return ServiceStatus(
                status=HealthStatus.DOWN,
                last_checked=checked_at,
                failure_reason=_failure_reason(error),
            )
        return ServiceStatus(
            status=HealthStatus.UP,
            last_checked=checked_at,
            response_time_ms=round((time.perf_counter() - start) * 1000, 2),
        )


async def _check_tcp(service: ServiceConfig) -> None:
    _, writer = await asyncio.open_connection(service.host, service.port)
    writer.close()


async def _check_ssh(service: ServiceConfig) -> None:
    reader, writer = await asyncio.open_connection(service.host, service.port)
    try:
        while line := await reader.readline():  # the server may send other lines before its banner
            if line.startswith(SSH_BANNER_PREFIX):
                return
    except ValueError as error:  # a line longer than the read buffer
        raise UnexpectedResponseError("SSH reply line too long") from error
    finally:
        writer.close()
    raise UnexpectedResponseError("no SSH banner")


async def _check_http(service: ServiceConfig) -> None:
    url = httpx2.URL(scheme=service.check_type, host=service.host, port=service.port, path="/")
    async with (
        httpx2.AsyncClient(timeout=None) as client,  # asyncio.timeout bounds the whole check
        client.stream("GET", url) as response,  # status and headers only, no body download
    ):
        if response.is_server_error:
            raise UnexpectedResponseError(f"HTTP {response.status_code}")


def _failure_reason(error: Exception) -> FailureReason:
    if isinstance(error, (UnexpectedResponseError, httpx2.RemoteProtocolError)):
        return FailureReason.UNEXPECTED_RESPONSE
    if isinstance(error, httpx2.TransportError):
        error = _root_cause(error)
    if isinstance(error, TimeoutError):
        return FailureReason.TIMEOUT
    if isinstance(error, ConnectionRefusedError):
        return FailureReason.CONNECTION_REFUSED
    if isinstance(error, socket.gaierror):
        return FailureReason.DNS_ERROR
    if isinstance(error, OSError) and error.errno in UNREACHABLE_ERRNOS:
        return FailureReason.UNREACHABLE
    return FailureReason.OTHER


def _root_cause(error: BaseException) -> BaseException:
    """httpx wraps the socket error several levels deep, through __cause__, __context__ and exception groups."""
    while True:
        if isinstance(error, BaseExceptionGroup):
            error = error.exceptions[0]
        elif (inner := error.__cause__ or error.__context__) is not None:
            error = inner
        else:
            return error
