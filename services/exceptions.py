from typing import Any


class AppException(Exception):
    """Base application exception returning structured JSON envelope."""

    default_code = "INTERNAL_SERVER_ERROR"
    default_status_code = 500
    default_message = "An unexpected error occurred."

    def __init__(
        self,
        message: str | None = None,
        code: str | None = None,
        status_code: int | None = None,
        details: Any = None,
    ):
        super().__init__(message or self.default_message)
        self.message = message or self.default_message
        self.code = code or self.default_code
        self.status_code = status_code or self.default_status_code
        self.details = details

    def to_dict(self) -> dict:
        result: dict[str, Any] = {
            "error": {
                "code": self.code,
                "message": self.message,
            }
        }
        if self.details is not None:
            result["error"]["details"] = self.details
        return result


class ValidationError(AppException):
    default_code = "VALIDATION_ERROR"
    default_status_code = 400
    default_message = "Invalid input data provided."


class NotFoundError(AppException):
    default_code = "RESOURCE_NOT_FOUND"
    default_status_code = 404
    default_message = "Requested resource was not found."


class UnauthorizedError(AppException):
    default_code = "UNAUTHORIZED"
    default_status_code = 401
    default_message = "Authentication required or invalid credentials."


class ForbiddenError(AppException):
    default_code = "FORBIDDEN"
    default_status_code = 403
    default_message = "You do not have permission to perform this action."


class ConflictError(AppException):
    default_code = "CONFLICT"
    default_status_code = 409
    default_message = "Resource state conflict."


class RateLimitError(AppException):
    default_code = "RATE_LIMIT_EXCEEDED"
    default_status_code = 429
    default_message = "Too many requests. Please try again later."


class ExternalServiceError(AppException):
    default_code = "EXTERNAL_SERVICE_ERROR"
    default_status_code = 502
    default_message = "External service request failed."


class InvalidStateTransitionError(AppException):
    default_code = "INVALID_STATE_TRANSITION"
    default_status_code = 400
    default_message = "The requested post status transition is invalid."
