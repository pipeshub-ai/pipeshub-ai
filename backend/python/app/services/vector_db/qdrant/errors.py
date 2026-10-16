"""Which Qdrant client errors a search may retry.

Qdrant reports its own search timeout as gRPC DEADLINE_EXCEEDED / HTTP 408 and
an unavailable shard as UNAVAILABLE / 503 (``lib/storage/src/content_manager/
conversions.rs`` and ``src/actix/helpers.rs`` in qdrant/qdrant); the client
raises the same codes for its own deadline and for a dropped connection.
"""

from __future__ import annotations

import grpc  # type: ignore
import httpx
from qdrant_client.http.exceptions import (  # type: ignore
    ResponseHandlingException,
    UnexpectedResponse,
)

from app.services.vector_db.models import SearchFailureKind

_GRPC_TRANSIENT = {
    grpc.StatusCode.DEADLINE_EXCEEDED: SearchFailureKind.TIMEOUT,
    grpc.StatusCode.UNAVAILABLE: SearchFailureKind.UNAVAILABLE,
}

_HTTP_TRANSIENT = {
    408: SearchFailureKind.TIMEOUT,
    502: SearchFailureKind.UNAVAILABLE,
    503: SearchFailureKind.UNAVAILABLE,
    504: SearchFailureKind.TIMEOUT,
}


def classify_qdrant_search_error(error: BaseException) -> SearchFailureKind | None:
    if isinstance(error, grpc.aio.AioRpcError):
        return _GRPC_TRANSIENT.get(error.code())
    if isinstance(error, UnexpectedResponse):
        return _HTTP_TRANSIENT.get(error.status_code or 0)
    if isinstance(error, ResponseHandlingException):
        source = error.source
        if isinstance(source, httpx.TimeoutException):
            return SearchFailureKind.TIMEOUT
        if isinstance(source, httpx.NetworkError):
            return SearchFailureKind.UNAVAILABLE
    return None
