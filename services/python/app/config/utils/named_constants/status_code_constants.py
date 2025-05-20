from enum import Enum


class StatusCodeConstants(Enum):
    """Constants for status codes"""

    SUCCESS = 200
    FAIL = 500
    UNHEALTHY = 503
    NOT_FOUND = 404
