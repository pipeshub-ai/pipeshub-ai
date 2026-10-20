"""Which opensearch-py errors a search may retry.

The transport already retries a ``ConnectionError`` and 502/503/504 against
other nodes, but not a ``ConnectionTimeout`` (``retry_on_timeout`` defaults to
False), so one more attempt here is what covers a slow cluster.
"""

from __future__ import annotations

from opensearchpy.exceptions import (  # type: ignore
    ConnectionError as OpenSearchConnectionError,
)
from opensearchpy.exceptions import (  # type: ignore
    ConnectionTimeout,
    SSLError,
    TransportError,
)

from app.services.vector_db.models import SearchFailureKind

_HTTP_TRANSIENT = {
    502: SearchFailureKind.UNAVAILABLE,
    503: SearchFailureKind.UNAVAILABLE,
    504: SearchFailureKind.TIMEOUT,
}


def classify_opensearch_search_error(error: BaseException) -> SearchFailureKind | None:
    if isinstance(error, ConnectionTimeout):
        return SearchFailureKind.TIMEOUT
    if isinstance(error, SSLError):
        # A certificate problem fails the same way every time.
        return None
    if isinstance(error, OpenSearchConnectionError):
        return SearchFailureKind.UNAVAILABLE
    if isinstance(error, TransportError) and isinstance(error.status_code, int):
        return _HTTP_TRANSIENT.get(error.status_code)
    return None
