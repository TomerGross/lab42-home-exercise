"""Domain errors raised by the business logic. Each one declares how it is reported to the client."""

from http import HTTPStatus


class ServiceMonitorError(Exception):
    """Base for errors caused by the client's request."""

    status_code: HTTPStatus
    code: str


class UnknownServiceError(ServiceMonitorError):
    status_code = HTTPStatus.NOT_FOUND
    code = "unknown_service"

    def __init__(self, name: str):
        super().__init__(f"Unknown service: '{name}'")


class DuplicateServiceError(ServiceMonitorError):
    status_code = HTTPStatus.CONFLICT
    code = "duplicate_service"

    def __init__(self, conflict: str):
        super().__init__(f"Duplicate service: {conflict}")
