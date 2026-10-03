"""Requests whose Host header tries to replace the path an auth middleware reads."""

from starlette.requests import Request

POISONED_HOSTS = ["evil.test/health?", "evil.test/health#", "a@b/health?x="]


def request_with_host(path: str, host: str = "testserver") -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "query_string": b"",
            "headers": [(b"host", host.encode("latin-1"))],
            "scheme": "http",
            "server": ("testserver", 80),
        }
    )
