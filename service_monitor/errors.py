"""Domain errors raised by the business logic; the HTTP layer maps them to status codes."""


class UnknownServiceError(Exception):
    def __init__(self, name: str):
        super().__init__(f"Unknown service: '{name}'")


class DuplicateServiceError(Exception):
    def __init__(self, conflict: str):
        super().__init__(f"Duplicate service: {conflict}")
