import asyncio
import errno
import socket

import pytest

from service_monitor.schemas import FailureReason, HealthStatus, ServiceConfig
from service_monitor.service_checker import ServiceChecker, TcpServiceChecker
from tests.helpers import closed_port

TIMEOUT_SECONDS = 1.0


def make_config(port: int, host: str = "127.0.0.1") -> ServiceConfig:
    return ServiceConfig(name="Service A", host=host, port=port)


@pytest.fixture
async def listening_port():
    server = await asyncio.start_server(lambda reader, writer: writer.close(), "127.0.0.1", 0)
    yield server.sockets[0].getsockname()[1]
    server.close()
    await server.wait_closed()


def test_tcp_checker_implements_the_checker_contract():
    assert isinstance(TcpServiceChecker(TIMEOUT_SECONDS), ServiceChecker)
    with pytest.raises(TypeError):
        ServiceChecker()


async def test_open_port_is_up(listening_port):
    status = await TcpServiceChecker(TIMEOUT_SECONDS).check(make_config(listening_port))

    assert status.status == HealthStatus.UP
    assert status.response_time_ms >= 0
    assert status.failure_reason is None


async def test_last_checked_is_utc_in_whole_seconds(listening_port):
    status = await TcpServiceChecker(TIMEOUT_SECONDS).check(make_config(listening_port))

    assert status.last_checked.utcoffset().total_seconds() == 0
    assert status.last_checked.microsecond == 0


async def test_closed_port_is_refused():
    # Windows needs ~2s to report a refused localhost connection, hence the generous timeout.
    status = await TcpServiceChecker(timeout_seconds=5).check(make_config(closed_port()))

    assert status.status == HealthStatus.DOWN
    assert status.failure_reason == FailureReason.CONNECTION_REFUSED
    assert status.response_time_ms is None


async def test_slow_connection_times_out(monkeypatch):
    async def hang(*args, **kwargs):
        await asyncio.sleep(10)

    monkeypatch.setattr(asyncio, "open_connection", hang)

    status = await TcpServiceChecker(timeout_seconds=0.05).check(make_config(8001))

    assert status.status == HealthStatus.DOWN
    assert status.failure_reason == FailureReason.TIMEOUT


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (socket.gaierror(socket.EAI_NONAME, "Name or service not known"), FailureReason.DNS_ERROR),
        (OSError(errno.EHOSTUNREACH, "No route to host"), FailureReason.UNREACHABLE),
        (OSError(errno.ENETUNREACH, "Network is unreachable"), FailureReason.UNREACHABLE),
        (OSError(10065, "A socket operation was attempted to an unreachable host"), FailureReason.UNREACHABLE),
        (OSError(errno.EACCES, "Permission denied"), FailureReason.OTHER),
    ],
)
async def test_connection_errors_are_classified(monkeypatch, error, reason):
    async def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(asyncio, "open_connection", fail)

    status = await TcpServiceChecker(TIMEOUT_SECONDS).check(make_config(8001, host="example.com"))

    assert status.status == HealthStatus.DOWN
    assert status.failure_reason == reason
