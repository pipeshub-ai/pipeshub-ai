from ..backend import NOOP

HTTP_REQUESTS = NOOP
HTTP_REQUEST_DURATION = NOOP


def bound_route(route: str) -> str:
    return route
