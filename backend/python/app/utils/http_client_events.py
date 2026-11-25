"""Announces an outbound HTTP request as the ``http.client.request.completed`` platform event.

Called by the HTTP client wrappers the application sends through: ``HTTPClient.execute``
(every connector and external source client), ``BaseServiceClient`` (the parsing,
extraction and docling services), ``GraphQLClient.execute`` and ``make_api_call`` (a
service's own API). The facts are a bounded name of who was called, the method, the
class of the answer, how long it took and which HTTP library sent it. Never a URL, a
header or a body. A request made with a client of its own, outside these wrappers, is
not announced.
"""

from __future__ import annotations

import time

from app import platform_events

COMPLETED = "http.client.request.completed"
NO_RESPONSE = "error"
_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})
_FIRST_STATUS, _LAST_STATUS = 100, 599


def status_class(status: object) -> str:
    """``2xx`` ... ``5xx`` for an HTTP status, ``error`` when no answer came."""
    if isinstance(status, bool) or not isinstance(status, int) or not _FIRST_STATUS <= status <= _LAST_STATUS:
        return NO_RESPONSE
    return f"{status // 100}xx"


def upstream_of(client: object) -> str:
    """Who a client class talks to, from where its class is defined: ``jira`` for a class in
    ``app.sources.client.jira.*``, else the class's own name. Set by code, so bounded."""
    cls = type(client)
    parts = cls.__module__.split(".")
    if parts[:3] == ["app", "sources", "client"] and len(parts) > 3 and parts[3] != "http":
        return parts[3]
    return cls.__name__


def completed(upstream: str, method: str, started: float, status: object = None, *, library: str = "httpx") -> None:
    """One request ended: answered with ``status``, or failed with none. ``started``: ``time.perf_counter()`` before it."""
    if not platform_events.has_listeners(COMPLETED):
        return
    verb = method.upper() if isinstance(method, str) else ""
    platform_events.emit(
        "http.client.request.completed",
        upstream=upstream,
        method=verb if verb in _METHODS else "OTHER",
        status_class=status_class(status),
        duration_seconds=time.perf_counter() - started,
        library=library,
    )


class request:  # lower case: used as ``with request(...) as call``
    """Times the enclosed request and announces it when the block ends. Set ``call.status`` once it is answered;
    left unset (the block raised, or no answer came) it is announced as ``error``."""

    __slots__ = ("upstream", "method", "library", "status", "started")

    def __init__(self, upstream: str, method: str, *, library: str) -> None:
        self.upstream = upstream
        self.method = method
        self.library = library
        self.status: object = None

    def __enter__(self) -> request:
        self.started = time.perf_counter()
        return self

    def __exit__(self, *_exc: object) -> None:
        completed(self.upstream, self.method, self.started, self.status, library=self.library)
