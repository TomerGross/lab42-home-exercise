"""Data shapes shared by every layer: API, business logic, checker and database.

ServiceConfig is what a client sends, ServiceStatus is the result of one check,
and Service is both: a stored service with its latest status.
"""

import ipaddress
import re
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_SERVICE_NAME_LENGTH = 100
MAX_HOSTNAME_LENGTH = 253  # DNS limit for a full hostname (RFC 1035)
MAX_HOSTNAME_LABEL_LENGTH = 63  # DNS limit for each dot-separated label (RFC 1035)
MAX_PORT = 65535  # TCP ports are 16-bit numbers

# Letters, digits and hyphens, not starting or ending with a hyphen (RFC 1123).
HOSTNAME_LABEL_PATTERN = re.compile(rf"(?!-)[a-z0-9-]{{1,{MAX_HOSTNAME_LABEL_LENGTH}}}(?<!-)")


class HealthStatus(StrEnum):
    """A service's state: UP or DOWN after a check, UNKNOWN until its first one."""

    UP = "UP"
    DOWN = "DOWN"
    UNKNOWN = "UNKNOWN"


class CheckType(StrEnum):
    """How a service is checked. The README lists what UP means for each type."""

    TCP = "tcp"
    HTTP = "http"
    HTTPS = "https"
    SSH = "ssh"


class FailureReason(StrEnum):
    """Why a check reported DOWN."""

    TIMEOUT = "timeout"
    CONNECTION_REFUSED = "connection_refused"
    UNREACHABLE = "unreachable"
    DNS_ERROR = "dns_error"
    UNEXPECTED_RESPONSE = "unexpected_response"
    OTHER = "other"


class ServiceConfig(BaseModel):
    """A service to monitor, as sent by the client."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=MAX_SERVICE_NAME_LENGTH, pattern=r"^[^/]+$")  # no "/": it is in the URL
    host: str = Field(min_length=1, max_length=MAX_HOSTNAME_LENGTH)
    port: int = Field(ge=1, le=MAX_PORT, strict=True)  # strict: "8001" and True are not ports
    check_type: CheckType = CheckType.TCP

    @field_validator("host")
    @classmethod
    def validate_host(cls, host: str) -> str:
        """Return the host in lowercase if it is an IP address or a valid hostname.

        A hostname's last label must not be all digits, so a mistyped IPv4
        address such as 256.1.1.1 is rejected rather than taken for a hostname.
        Raises ValueError otherwise.
        """
        host = host.lower()
        try:
            ipaddress.ip_address(host)
            return host
        except ValueError:
            pass
        labels = host.split(".")
        if all(HOSTNAME_LABEL_PATTERN.fullmatch(label) for label in labels) and not labels[-1].isdigit():
            return host
        raise ValueError("must be a valid IP address or hostname")


class ServiceStatus(BaseModel):
    """The result of one check. The defaults describe a service that was never checked."""

    model_config = ConfigDict(from_attributes=True)  # can be built from a database row

    status: HealthStatus = HealthStatus.UNKNOWN
    last_checked: datetime | None = None
    response_time_ms: float | None = None
    failure_reason: FailureReason | None = None


class Service(ServiceStatus, ServiceConfig):
    """A configured service with its latest status, as returned by the API."""
