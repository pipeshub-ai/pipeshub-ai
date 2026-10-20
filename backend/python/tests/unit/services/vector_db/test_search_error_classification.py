"""Which client errors each provider calls transient for a search.

A retry is worth it only for a timeout or a store that is briefly unreachable;
a bad request or a credentials failure would fail the same way again.
"""

import grpc
import httpx
import pytest
from opensearchpy.exceptions import (
    AuthenticationException,
    AuthorizationException,
    ConnectionTimeout,
    NotFoundError,
    RequestError,
    SSLError,
    TransportError,
)
from opensearchpy.exceptions import (
    ConnectionError as OpenSearchConnectionError,
)
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse
from redis.exceptions import (
    AuthenticationError,
    AuthorizationError,
    BusyLoadingError,
    ResponseError,
)
from redis.exceptions import (
    ConnectionError as RedisConnectionError,
)
from redis.exceptions import (
    TimeoutError as RedisTimeoutError,
)

from app.services.vector_db.models import SearchFailureKind
from app.services.vector_db.opensearch.config import OpenSearchConfig
from app.services.vector_db.opensearch.errors import classify_opensearch_search_error
from app.services.vector_db.opensearch.opensearch import OpenSearchService
from app.services.vector_db.qdrant.config import QdrantConfig
from app.services.vector_db.qdrant.errors import classify_qdrant_search_error
from app.services.vector_db.qdrant.qdrant import QdrantService
from app.services.vector_db.redis.config import RedisVectorConfig
from app.services.vector_db.redis.errors import classify_redis_search_error
from app.services.vector_db.redis.redis_vector import RedisVectorService

TIMEOUT = SearchFailureKind.TIMEOUT
UNAVAILABLE = SearchFailureKind.UNAVAILABLE


def _rpc_error(code: grpc.StatusCode) -> grpc.aio.AioRpcError:
    return grpc.aio.AioRpcError(
        code, grpc.aio.Metadata(), grpc.aio.Metadata(), details="Operation 'search' timed out",
    )


def _http_error(status: int) -> UnexpectedResponse:
    return UnexpectedResponse(status_code=status, reason_phrase="", content=b"", headers=httpx.Headers())


@pytest.mark.parametrize(("error", "expected"), [
    (_rpc_error(grpc.StatusCode.DEADLINE_EXCEEDED), TIMEOUT),
    (_rpc_error(grpc.StatusCode.UNAVAILABLE), UNAVAILABLE),
    (_rpc_error(grpc.StatusCode.INVALID_ARGUMENT), None),
    (_rpc_error(grpc.StatusCode.UNAUTHENTICATED), None),
    (_rpc_error(grpc.StatusCode.PERMISSION_DENIED), None),
    (_rpc_error(grpc.StatusCode.NOT_FOUND), None),
    (_rpc_error(grpc.StatusCode.INTERNAL), None),
    (_http_error(408), TIMEOUT),
    (_http_error(504), TIMEOUT),
    (_http_error(502), UNAVAILABLE),
    (_http_error(503), UNAVAILABLE),
    (_http_error(400), None),
    (_http_error(401), None),
    (_http_error(403), None),
    (_http_error(404), None),
    (_http_error(500), None),
    (ResponseHandlingException(httpx.ReadTimeout("read timed out")), TIMEOUT),
    (ResponseHandlingException(httpx.ConnectTimeout("connect timed out")), TIMEOUT),
    (ResponseHandlingException(httpx.ConnectError("refused")), UNAVAILABLE),
    (ResponseHandlingException(ValueError("bad json")), None),
    (ValueError("Vector dimension error"), None),
])
def test_qdrant(error, expected) -> None:
    assert classify_qdrant_search_error(error) is expected
    assert QdrantService(QdrantConfig(host="localhost", port=6333)).transient_search_error(error) is expected


@pytest.mark.parametrize(("error", "expected"), [
    (ConnectionTimeout("TIMEOUT", "read timed out", TimeoutError()), TIMEOUT),
    (OpenSearchConnectionError("N/A", "refused", OSError()), UNAVAILABLE),
    (SSLError("N/A", "certificate verify failed", OSError()), None),
    (TransportError(502, "bad gateway"), UNAVAILABLE),
    (TransportError(503, "unavailable"), UNAVAILABLE),
    (TransportError(504, "gateway timeout"), TIMEOUT),
    (TransportError(500, "internal"), None),
    (RequestError(400, "search_phase_execution_exception"), None),
    (AuthenticationException(401, "unauthorized"), None),
    (AuthorizationException(403, "forbidden"), None),
    (NotFoundError(404, "index_not_found_exception"), None),
    (ValueError("bad body"), None),
])
def test_opensearch(error, expected) -> None:
    assert classify_opensearch_search_error(error) is expected
    service = OpenSearchService(OpenSearchConfig(host="localhost", port=9200))
    assert service.transient_search_error(error) is expected


@pytest.mark.parametrize(("error", "expected"), [
    (RedisTimeoutError("Timeout reading from socket"), TIMEOUT),
    (RedisConnectionError("Connection refused"), UNAVAILABLE),
    (BusyLoadingError("Redis is loading the dataset in memory"), UNAVAILABLE),
    # Both subclass ConnectionError in redis-py; neither is fixed by asking again.
    (AuthenticationError("invalid password"), None),
    (AuthorizationError("NOPERM"), None),
    (ResponseError("Syntax error at offset 3"), None),
    (ValueError("bad vector"), None),
])
def test_redis(error, expected) -> None:
    assert classify_redis_search_error(error) is expected
    service = RedisVectorService(RedisVectorConfig(host="localhost", port=6379))
    assert service.transient_search_error(error) is expected
