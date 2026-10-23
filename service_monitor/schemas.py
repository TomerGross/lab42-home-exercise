"""Data shapes shared by every layer: API, business logic, checker and database."""

import ipaddress
import re
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_SERVICE_NAME_LENGTH = 100  # our own limit
MAX_HOSTNAME_LENGTH = 253  # DNS limit for a full hostname (RFC 1035)
MAX_HOSTNAME_LABEL_LENGTH = 63  # DNS limit for each dot-separated label (RFC 1035)
MAX_PORT = 65535  # TCP ports are 16-bit numbers

HOSTNAME_LABEL = re.compile(rf"(?!-)[a-z0-9-]{{1,{MAX_HOSTNAME_LABEL_LENGTH}}}(?<!-)")


class HealthStatus(StrEnum):
    UP = "UP"
    DOWN = "DOWN"
    UNKNOWN = "UNKNOWN"


class FailureReason(StrEnum):
    TIMEOUT = "timeout"
    CONNECTION_REFUSED = "connection_refused"
    UNREACHABLE = "unreachable"
    DNS_ERROR = "dns_error"
    OTHER = "other"


class ServiceConfig(BaseModel):
    """A service to monitor, as sent by the client."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=MAX_SERVICE_NAME_LENGTH, pattern=r"^[^/]+$")
    host: str = Field(min_length=1, max_length=MAX_HOSTNAME_LENGTH)
    port: int = Field(ge=1, le=MAX_PORT, strict=True)

    @field_validator("host")
    @classmethod
    def validate_host(cls, host: str) -> str:
        host = host.lower()
        try:
            ipaddress.ip_address(host)
            return host
        except ValueError:
            pass
        labels = host.split(".")
        if all(HOSTNAME_LABEL.fullmatch(label) for label in labels) and not labels[-1].isdigit():
            return host
        raise ValueError("must be a valid IP address or hostname")


class ServiceStatus(BaseModel):
    """Result of one health check."""

    model_config = ConfigDict(from_attributes=True)  # can be built from a database row

    status: HealthStatus = HealthStatus.UNKNOWN
    last_checked: datetime | None = None
    response_time_ms: float | None = None
    failure_reason: FailureReason | None = None


class Service(ServiceStatus, ServiceConfig):
    """A configured service with its latest status, as returned by the API."""
