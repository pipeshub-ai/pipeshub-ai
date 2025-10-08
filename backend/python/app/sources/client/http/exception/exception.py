from enum import Enum
from typing import Dict, Optional

from fastapi import HTTPException as FastAPIHTTPException  # type: ignore


class HttpStatusCode(Enum):
    """Constants for status codes"""

    SUCCESS = 200
    CREATED = 201
    NO_CONTENT = 204

    BAD_REQUEST = 400
    UNAUTHORIZED = 401
    FORBIDDEN = 403
    NOT_FOUND = 404
    METHOD_NOT_ALLOWED = 405
    CONFLICT = 409
    UNPROCESSABLE_ENTITY = 422
    TOO_MANY_REQUESTS = 429

    INTERNAL_SERVER_ERROR = 500
    BAD_GATEWAY = 502
    SERVICE_UNAVAILABLE = 503



class HTTPException(FastAPIHTTPException):
    """HTTPException
    Args:
        status_code: The status code of the exception
        message: The message of the exception
        headers: The headers of the exception
    """
    def __init__(self, status_code: int, message: str = "", headers: Optional[Dict[str, str]] = None) -> None:
        super().__init__(status_code, message, headers)


class BadRequestError(HTTPException):
    """BadRequestError
    Args:
        message: The message of the exception
    """
    def __init__(self, message: str = "Bad Request") -> None:
        super().__init__(HttpStatusCode.BAD_REQUEST.value, message)


class UnauthorizedError(HTTPException):
    """UnauthorizedError
    Args:
        message: The message of the exception
    """
    def __init__(self, message: str = "Unauthorized") -> None:
        super().__init__(HttpStatusCode.UNAUTHORIZED.value, message)


class ForbiddenError(HTTPException):
    """ForbiddenError
    Args:
        message: The message of the exception
    """
    def __init__(self, message: str = "Forbidden") -> None:
        super().__init__(HttpStatusCode.FORBIDDEN.value, message)


class NotFoundError(HTTPException):
    """NotFoundError
    Args:
        message: The message of the exception
    """
    def __init__(self, message: str = "Not Found") -> None:
        super().__init__(HttpStatusCode.NOT_FOUND.value, message)


class MethodNotAllowedError(HTTPException):
    """MethodNotAllowedError
    Args:
        message: The message of the exception
    """
    def __init__(self, message: str = "Method Not Allowed") -> None:
        super().__init__(HttpStatusCode.METHOD_NOT_ALLOWED.value, message)


class ConflictError(HTTPException):
    """ConflictError
    Args:
        message: The message of the exception
    """
    def __init__(self, message: str = "Conflict") -> None:
        super().__init__(HttpStatusCode.CONFLICT.value, message)


class UnprocessableEntityError(HTTPException):
    """UnprocessableEntityError
    Args:
        message: The message of the exception
    """
    def __init__(self, message: str = "Unprocessable Entity") -> None:
        super().__init__(HttpStatusCode.UNPROCESSABLE_ENTITY.value, message)


class TooManyRequestsError(HTTPException):
    """TooManyRequestsError
    Args:
        message: The message of the exception
    """
    def __init__(self, message: str = "Too Many Requests") -> None:
        super().__init__(HttpStatusCode.TOO_MANY_REQUESTS.value, message)


class InternalServerError(HTTPException):
    """InternalServerError
    Args:
        message: The message of the exception
    """
    def __init__(self, message: str = "Internal Server Error") -> None:
        super().__init__(HttpStatusCode.INTERNAL_SERVER_ERROR.value, message)


class BadGatewayError(HTTPException):
    """BadGatewayError
    Args:
        message: The message of the exception
    """
    def __init__(self, message: str = "Bad Gateway") -> None:
        super().__init__(HttpStatusCode.BAD_GATEWAY.value, message)


class ServiceUnavailableError(HTTPException):
    """ServiceUnavailableError
    Args:
        message: The message of the exception
    """
    def __init__(self, message: str = "Service Unavailable") -> None:
        super().__init__(HttpStatusCode.SERVICE_UNAVAILABLE.value, message)

class VectorDBEmptyError(ValueError):
    pass
