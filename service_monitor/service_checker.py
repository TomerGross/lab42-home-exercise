"""Checks whether a service is reachable over TCP, HTTP(S) or SSH.

Knows nothing about the API or the database: it takes a ServiceConfig and
returns a ServiceStatus.
"""

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
    """The contract the business logic depends on, so tests can pass a fake checker."""

    @abstractmethod
    async def check(self, service: ServiceConfig) -> ServiceStatus:
        """Probe the service once and return its status. Must not raise on network failures."""


class NetworkServiceChecker(ServiceChecker):
    """Checks services over the network, each check limited to `timeout_seconds`.

    A service is UP when it answers its check type in time: tcp opens a
    connection, ssh sends an SSH banner, http and https answer GET / with a
    status below 500.
    """

    def __init__(self, timeout_seconds: float):
        self.timeout_seconds = timeout_seconds

    async def check(self, service: ServiceConfig) -> ServiceStatus:
        """Run the check that matches the service's check type and return UP or DOWN.

        Every network failure becomes DOWN with a failure reason, never an
        exception. The response time is measured for UP only.
        """
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
    """Open a TCP connection and close it again."""
    _, writer = await asyncio.open_connection(service.host, service.port)
    writer.close()


async def _check_ssh(service: ServiceConfig) -> None:
    """Read the server's lines until one starts with "SSH-". Sends nothing, so no SSH session starts.

    Raises UnexpectedResponseError if the server closes the connection without one.
    """
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
    """Send GET / and raise UnexpectedResponseError if the status is 5xx.

    Redirects are not followed and certificates are verified. The client has no
    timeout of its own, since the check's timeout covers it, and it ignores proxy
    settings: like the other checks, it connects to the service directly.
    """
    url = httpx2.URL(scheme=service.check_type, host=service.host, port=service.port, path="/")
    async with (
        httpx2.AsyncClient(timeout=None, trust_env=False) as client,
        client.stream("GET", url) as response,  # status and headers only, no body download
    ):
        if response.is_server_error:
            raise UnexpectedResponseError(f"HTTP {response.status_code}")


def _failure_reason(error: Exception) -> FailureReason:
    """Translate the error a check raised into the reason reported to the client."""
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
    """Return the original error inside an httpx error.

    httpx wraps the socket error several levels deep, through __cause__,
    __context__ and exception groups.
    """
    while True:
        if isinstance(error, BaseExceptionGroup):
            error = error.exceptions[0]
        elif (inner := error.__cause__ or error.__context__) is not None:
            error = inner
        else:
            return error
