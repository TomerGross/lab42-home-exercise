"""Tests for service_monitor/service_checker.py: each check type against real local servers.

Timeouts and the rarer network errors cannot be produced reliably on localhost,
so those tests replace asyncio.open_connection with one that hangs or fails.
"""

import asyncio
import errno
import socket
from contextlib import asynccontextmanager

import pytest

from service_monitor.schemas import CheckType, FailureReason, HealthStatus, ServiceConfig
from service_monitor.service_checker import NetworkServiceChecker, ServiceChecker
from tests.helpers import closed_port

TIMEOUT_SECONDS = 1.0


def make_config(port: int, host: str = "127.0.0.1", check_type: CheckType = CheckType.TCP) -> ServiceConfig:
    """Return a service on `host:port` with the given check type."""
    return ServiceConfig(name="Service A", host=host, port=port, check_type=check_type)


def http_reply(status: str, headers: str = "") -> bytes:
    """Return a minimal HTTP response with the given status line and extra headers."""
    return f"HTTP/1.1 {status}\r\n{headers}Content-Length: 0\r\n\r\n".encode()


@asynccontextmanager
async def fake_server(reply: bytes | None, wait_for_request: bool = False):
    """Run a local server that answers every client with `reply`, or stays silent when `reply` is None.

    Yields the server's port. With `wait_for_request`, it first reads the client's HTTP request.
    """

    async def answer(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if wait_for_request:
            await reader.readuntil(b"\r\n\r\n")
        if reply is None:
            await reader.read()  # silent until the client gives up
        else:
            writer.write(reply)
            await writer.drain()
        writer.close()

    server = await asyncio.start_server(answer, "127.0.0.1", 0)
    try:
        yield server.sockets[0].getsockname()[1]
    finally:
        server.close()
        await server.wait_closed()


def test_network_checker_implements_the_checker_contract():
    assert isinstance(NetworkServiceChecker(TIMEOUT_SECONDS), ServiceChecker)
    with pytest.raises(TypeError):
        ServiceChecker()


async def test_open_port_is_up():
    async with fake_server(b"") as port:
        status = await NetworkServiceChecker(TIMEOUT_SECONDS).check(make_config(port))

    assert status.status == HealthStatus.UP
    assert status.response_time_ms >= 0
    assert status.failure_reason is None


async def test_last_checked_is_utc_in_whole_seconds():
    async with fake_server(b"") as port:
        status = await NetworkServiceChecker(TIMEOUT_SECONDS).check(make_config(port))

    assert status.last_checked.utcoffset().total_seconds() == 0
    assert status.last_checked.microsecond == 0


async def test_closed_port_is_refused():
    # Windows needs ~2s to report a refused localhost connection, hence the generous timeout.
    status = await NetworkServiceChecker(timeout_seconds=5).check(make_config(closed_port()))

    assert status.status == HealthStatus.DOWN
    assert status.failure_reason == FailureReason.CONNECTION_REFUSED
    assert status.response_time_ms is None


async def test_slow_connection_times_out(monkeypatch):
    async def hang(*args, **kwargs):
        await asyncio.sleep(10)

    monkeypatch.setattr(asyncio, "open_connection", hang)

    status = await NetworkServiceChecker(timeout_seconds=0.05).check(make_config(8001))

    assert status.status == HealthStatus.DOWN
    assert status.failure_reason == FailureReason.TIMEOUT


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (socket.gaierror(socket.EAI_NONAME, "Name or service not known"), FailureReason.DNS_ERROR),
        (OSError(errno.EHOSTUNREACH, "No route to host"), FailureReason.UNREACHABLE),
        (OSError(errno.ENETUNREACH, "Network is unreachable"), FailureReason.UNREACHABLE),
        (OSError(errno.EACCES, "Permission denied"), FailureReason.OTHER),
    ],
)
async def test_connection_errors_are_classified(monkeypatch, error, reason):
    async def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(asyncio, "open_connection", fail)

    status = await NetworkServiceChecker(TIMEOUT_SECONDS).check(make_config(8001, host="example.com"))

    assert status.status == HealthStatus.DOWN
    assert status.failure_reason == reason


@pytest.mark.parametrize(
    "reply",
    [
        http_reply("200 OK"),
        http_reply("404 Not Found"),
        http_reply("301 Moved Permanently", headers=f"Location: http://127.0.0.1:{closed_port()}/\r\n"),
    ],
    ids=["200", "404", "301-not-followed"],
)
async def test_http_server_answering_below_500_is_up(reply):
    async with fake_server(reply, wait_for_request=True) as port:
        status = await NetworkServiceChecker(TIMEOUT_SECONDS).check(make_config(port, check_type=CheckType.HTTP))

    assert status.status == HealthStatus.UP
    assert status.response_time_ms >= 0


async def test_http_check_ignores_proxy_settings(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", f"http://127.0.0.1:{closed_port()}")
    monkeypatch.delenv("NO_PROXY", raising=False)

    async with fake_server(http_reply("200 OK"), wait_for_request=True) as port:
        status = await NetworkServiceChecker(TIMEOUT_SECONDS).check(make_config(port, check_type=CheckType.HTTP))

    assert status.status == HealthStatus.UP


@pytest.mark.parametrize("reply", [http_reply("503 Service Unavailable"), b"hello\r\n\r\n"], ids=["503", "not-http"])
async def test_http_server_error_or_non_http_reply_is_unexpected_response(reply):
    async with fake_server(reply, wait_for_request=True) as port:
        status = await NetworkServiceChecker(TIMEOUT_SECONDS).check(make_config(port, check_type=CheckType.HTTP))

    assert status.status == HealthStatus.DOWN
    assert status.failure_reason == FailureReason.UNEXPECTED_RESPONSE


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost"])
async def test_http_closed_port_is_refused(host):
    # Windows needs ~2s per address to report a refused localhost connection, hence the generous timeout.
    config = make_config(closed_port(), host=host, check_type=CheckType.HTTP)

    status = await NetworkServiceChecker(timeout_seconds=10).check(config)

    assert status.failure_reason == FailureReason.CONNECTION_REFUSED


async def test_http_unknown_host_is_dns_error():
    config = make_config(80, host="no-such-host.invalid", check_type=CheckType.HTTP)

    status = await NetworkServiceChecker(timeout_seconds=10).check(config)

    assert status.failure_reason == FailureReason.DNS_ERROR


async def test_https_check_uses_tls():
    # A plain-HTTP server cannot complete a TLS handshake, so HTTPS against it must fail.
    async with fake_server(http_reply("200 OK")) as port:
        status = await NetworkServiceChecker(TIMEOUT_SECONDS).check(make_config(port, check_type=CheckType.HTTPS))

    assert status.status == HealthStatus.DOWN


@pytest.mark.parametrize(
    "reply",
    [b"SSH-2.0-OpenSSH_9.6\r\n", b"Welcome\r\nSSH-2.0-OpenSSH_9.6\r\n"],
    ids=["banner", "lines-before-banner"],
)
async def test_ssh_banner_is_up(reply):
    async with fake_server(reply) as port:
        status = await NetworkServiceChecker(TIMEOUT_SECONDS).check(make_config(port, check_type=CheckType.SSH))

    assert status.status == HealthStatus.UP


async def test_ssh_banner_arriving_in_pieces_is_up():
    async def answer_in_two_pieces(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        for piece in (b"SS", b"H-2.0-OpenSSH_9.6\r\n"):
            writer.write(piece)
            await writer.drain()
            await asyncio.sleep(0.05)
        writer.close()

    server = await asyncio.start_server(answer_in_two_pieces, "127.0.0.1", 0)
    async with server:
        port = server.sockets[0].getsockname()[1]
        status = await NetworkServiceChecker(TIMEOUT_SECONDS).check(make_config(port, check_type=CheckType.SSH))

    assert status.status == HealthStatus.UP


@pytest.mark.parametrize(
    "reply",
    [b"220 smtp.example.com ESMTP\r\n", b"", b"x" * 100_000],
    ids=["other-protocol", "closed", "endless-line"],
)
async def test_ssh_without_banner_is_unexpected_response(reply):
    async with fake_server(reply) as port:
        status = await NetworkServiceChecker(TIMEOUT_SECONDS).check(make_config(port, check_type=CheckType.SSH))

    assert status.status == HealthStatus.DOWN
    assert status.failure_reason == FailureReason.UNEXPECTED_RESPONSE


@pytest.mark.parametrize("check_type", [CheckType.HTTP, CheckType.SSH])
async def test_silent_server_times_out(check_type):
    async with fake_server(None) as port:
        status = await NetworkServiceChecker(timeout_seconds=0.2).check(make_config(port, check_type=check_type))

    assert status.failure_reason == FailureReason.TIMEOUT
