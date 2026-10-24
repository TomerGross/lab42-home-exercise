"""Domain errors raised by the business logic and the database.

Each error declares the HTTP status and the error code it is reported with, so
the API layer needs one handler for all of them.
"""

from http import HTTPStatus


class ServiceMonitorError(Exception):
    """Base for errors caused by the client's request."""

    status_code: HTTPStatus
    code: str  # stable error code in the response body, for programs


class UnknownServiceError(ServiceMonitorError):
    """No service has this name."""

    status_code = HTTPStatus.NOT_FOUND
    code = "unknown_service"

    def __init__(self, name: str):
        super().__init__(f"Unknown service: '{name}'")


class DuplicateServiceError(ServiceMonitorError):
    """A service with the same name or the same host:port already exists, or appears twice in one request."""

    status_code = HTTPStatus.CONFLICT
    code = "duplicate_service"

    def __init__(self):
        super().__init__("A service with this name or host:port already exists")
