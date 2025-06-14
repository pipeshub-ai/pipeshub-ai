from enum import Enum


class HttpStatusCode(Enum):
    """Constants for status codes"""

    SUCCESS = 200
    NO_CONTENT = 204

    BAD_REQUEST = 400
    UNAUTHORIZED = 401
    FORBIDDEN = 403
    NOT_FOUND = 404
    TOO_MANY_REQUESTS = 429

    INTERNAL_SERVER_ERROR = 500
    UNHEALTHY = 503
