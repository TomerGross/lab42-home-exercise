import pytest
from pydantic import ValidationError

from service_monitor.schemas import HealthStatus, Service, ServiceConfig


def make_config(**overrides):
    data = {"name": "Service A", "host": "127.0.0.1", "port": 8001} | overrides
    return ServiceConfig(**data)


def test_name_and_host_are_normalized():
    config = make_config(name="  Service A  ", host="  Example.COM ")
    assert config.name == "Service A"
    assert config.host == "example.com"


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost", "db-1.internal.example.com"])
def test_accepts_ips_and_hostnames(host):
    assert make_config(host=host).host == host


@pytest.mark.parametrize(
    "host",
    [
        "",
        "   ",
        "http://example.com",
        "example.com:80",
        "foo bar",
        "-bad.com",
        "bad-.com",
        "a..b",
        "256.1.1.1",
        "a" * 64 + ".com",
    ],
)
def test_rejects_malformed_hosts(host):
    with pytest.raises(ValidationError):
        make_config(host=host)


@pytest.mark.parametrize("name", ["", "   ", "a/b", "x" * 101])
def test_rejects_invalid_names(name):
    with pytest.raises(ValidationError):
        make_config(name=name)


@pytest.mark.parametrize("port", [0, 65536, -1])
def test_rejects_out_of_range_ports(port):
    with pytest.raises(ValidationError):
        make_config(port=port)


@pytest.mark.parametrize("port", ["8001", True, 8001.5])
def test_port_must_be_an_integer(port):
    with pytest.raises(ValidationError):
        make_config(port=port)


def test_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        make_config(protocol="tcp")


def test_new_service_starts_unknown():
    service = Service(name="Service A", host="127.0.0.1", port=8001)
    assert service.status == HealthStatus.UNKNOWN
    assert service.last_checked is None
    assert service.response_time_ms is None
    assert service.failure_reason is None


def test_service_serializes_flat_like_the_assignment_example():
    service = Service(name="Service A", host="127.0.0.1", port=8001)
    assert list(service.model_dump()) == [
        "name", "host", "port", "status", "last_checked", "response_time_ms", "failure_reason",
    ]
