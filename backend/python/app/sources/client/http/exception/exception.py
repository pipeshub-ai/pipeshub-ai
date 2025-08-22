import uuid
from datetime import datetime

from fastapi import HTTPException as FastAPIHTTPException  # type: ignore


class HTTPException(FastAPIHTTPException):
    """HTTPException
    Args:
        status_code: The status code of the exception
        message: The message of the exception
        headers: The headers of the exception
    """
    def __init__(self, status_code: int, message: str = "", headers: dict = {}):
        super().__init__(status_code, message, headers)
        self.timestamp = datetime.utcnow()
        self.error_id = str(uuid.uuid4())


class BadRequestError(HTTPException):
    """BadRequestError
    Args:
        message: The message of the exception
    """
    def __init__(self, message: str = "Bad Request"):
        super().__init__(400, message)


class UnauthorizedError(HTTPException):
    """UnauthorizedError
    Args:
        message: The message of the exception
    """
    def __init__(self, message: str = "Unauthorized"):
        super().__init__(401, message)


class ForbiddenError(HTTPException):
    """ForbiddenError
    Args:
        message: The message of the exception
    """
    def __init__(self, message: str = "Forbidden"):
        super().__init__(403, message)


class NotFoundError(HTTPException):
    """NotFoundError
    Args:
        message: The message of the exception
    """
    def __init__(self, message: str = "Not Found"):
        super().__init__(404, message)


class MethodNotAllowedError(HTTPException):
    """MethodNotAllowedError
    Args:
        message: The message of the exception
    """
    def __init__(self, message: str = "Method Not Allowed"):
        super().__init__(405, message)


class ConflictError(HTTPException):
    """ConflictError
    Args:
        message: The message of the exception
    """
    def __init__(self, message: str = "Conflict"):
        super().__init__(409, message)


class UnprocessableEntityError(HTTPException):
    """UnprocessableEntityError
    Args:
        message: The message of the exception
    """
    def __init__(self, message: str = "Unprocessable Entity"):
        super().__init__(422, message)


class TooManyRequestsError(HTTPException):
    """TooManyRequestsError
    Args:
        message: The message of the exception
    """
    def __init__(self, message: str = "Too Many Requests"):
        super().__init__(429, message)


class InternalServerError(HTTPException):
    """InternalServerError
    Args:
        message: The message of the exception
    """
    def __init__(self, message: str = "Internal Server Error"):
        super().__init__(500, message)


class BadGatewayError(HTTPException):
    """BadGatewayError
    Args:
        message: The message of the exception
    """
    def __init__(self, message: str = "Bad Gateway"):
        super().__init__(502, message)


class ServiceUnavailableError(HTTPException):
    """ServiceUnavailableError
    Args:
        message: The message of the exception
    """
    def __init__(self, message: str = "Service Unavailable"):
        super().__init__(503, message)
