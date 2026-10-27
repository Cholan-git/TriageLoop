class DomainError(Exception):
    """A rejected operation with a user-facing message and an HTTP-style status."""

    def __init__(self, message: str, status: int = 400, code: str = "invalid_request",
                 details: dict | None = None):
        super().__init__(message)
        self.message, self.status, self.code, self.details = message, status, code, details or {}


class NotFound(DomainError):
    def __init__(self, message: str = "Not found"):
        super().__init__(message, 404, "not_found")


class Conflict(DomainError):
    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message, 409, "conflict", details)
