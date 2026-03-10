from enum import Enum


class HttpStatusCode(Enum):
    """Constants for HTTP status codes"""

    # 2xx Success
    OK = 200
    SUCCESS = 200  # Alias for OK
    CREATED = 201
    ACCEPTED = 202
    NO_CONTENT = 204
    PARTIAL_CONTENT = 206

    # 4xx Client Errors
    BAD_REQUEST = 400
    UNAUTHORIZED = 401
    FORBIDDEN = 403
    NOT_FOUND = 404
    CONFLICT = 409
    GONE = 410
    TOO_MANY_REQUESTS = 429

    # 5xx Server Errors
    INTERNAL_SERVER_ERROR = 500
    UNHEALTHY = 503
    CLOUDFLARE_NETWORK_ERROR = 520
