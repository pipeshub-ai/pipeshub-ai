from enum import Enum


class StatusCodeConstants(Enum):
    """Constants for status codes"""

    SUCCESS = 200
    NO_CONTENT = 204
    FAIL = 500
    UNHEALTHY = 503
    BAD_REQUEST = 400
    NOT_FOUND = 404
    FORBIDDEN = 403
