"""Message error classification for selective retry logic.

Classifies errors into two categories:
- TERMINAL: Don't retry, ACK immediately (bad message, parsing failure, etc.)
- TRANSIENT: Retry up to max_attempts (network, timeout, API unavailable)

Classification is based purely on exception type, not on database status.
Processing-based failures (parsing, chunking, extraction) are TERMINAL because
retrying will produce the same result. Infrastructure failures (network, API
unavailable, rate limits) are TRANSIENT because they may succeed on retry.

HTTP Status Code Classification:
- Non-retryable (4xx client errors): 400, 401, 403, 404, 413, 422
- Retryable (transient/server errors): 408, 429, 500, 502, 503, 504
"""
import asyncio
import json
from typing import Optional

from app.exceptions.indexing_exceptions import (
    BlockContainerValidationError,
    ChunkingError,
    DocumentProcessingError,
    ExtractionError,
    IndexingError,
    ProcessingError,
)

# Import aiohttp at module level for efficiency (cached in sys.modules)
try:
    import aiohttp
    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False


class MessageErrorType:
    """Error type constants for classification."""

    TERMINAL = "terminal"  # Don't retry, ACK immediately
    TRANSIENT = "transient"  # Retry up to max_attempts


# HTTP status codes that indicate permanent client errors - do not retry
_NON_RETRYABLE_HTTP_STATUSES = frozenset({
    400,  # Bad Request - malformed request
    401,  # Unauthorized - auth config issue
    403,  # Forbidden - permission denied
    404,  # Not Found - resource doesn't exist
    413,  # Payload Too Large - content too large
    422,  # Unprocessable Entity - validation failure
})

# HTTP status codes that indicate transient errors - worth retrying
_RETRYABLE_HTTP_STATUSES = frozenset({
    408,  # Request Timeout
    429,  # Too Many Requests (rate limit)
    500,  # Internal Server Error
    502,  # Bad Gateway
    503,  # Service Unavailable
    504,  # Gateway Timeout
    520,  # Cloudflare network error
})


def _extract_status_code(exc: Exception) -> Optional[int]:
    """Extract HTTP status code from various exception types.

    Supports:
    - ApiCallError (from app.utils.api_call)
    - httpx.HTTPStatusError
    - aiohttp.ClientResponseError
    - openai.APIStatusError
    - botocore.exceptions.ClientError
    - Generic exceptions with status_code attribute
    """
    # Check for status_code attribute (ApiCallError, generic)
    if hasattr(exc, "status_code") and exc.status_code is not None:
        return exc.status_code

    # Check for status attribute (some HTTP libraries)
    if hasattr(exc, "status") and exc.status is not None:
        return exc.status

    # httpx.HTTPStatusError
    if hasattr(exc, "response") and hasattr(exc.response, "status_code"):
        return exc.response.status_code

    # aiohttp.ClientResponseError only — .code on parser errors is not HTTP status
    try:
        import aiohttp

        if isinstance(exc, aiohttp.ClientResponseError):
            return exc.status
    except ImportError:
        pass

    # botocore.exceptions.ClientError
    if hasattr(exc, "response"):
        response = exc.response
        if isinstance(response, dict):
            error = response.get("Error", {})
            # AWS errors have Code as string, but HTTPStatusCode in ResponseMetadata
            metadata = response.get("ResponseMetadata", {})
            if "HTTPStatusCode" in metadata:
                return metadata["HTTPStatusCode"]

    return None


def _is_retryable_status(status_code: int) -> bool:
    """Check if an HTTP status code should be retried."""
    if status_code in _NON_RETRYABLE_HTTP_STATUSES:
        return False
    return status_code in _RETRYABLE_HTTP_STATUSES or status_code >= 500


def _get_root_cause(exc: Exception) -> Exception:
    """Walk the exception chain to find the root cause.
    
    When exceptions are wrapped (e.g., `raise Exception(...) from e`),
    the original exception is preserved in __cause__ or __context__.
    This function walks the chain to find the deepest original exception.
    
    Args:
        exc: The exception to inspect
        
    Returns:
        The root cause exception (or exc itself if no chain exists)
    """
    root = exc
    for current in _iter_exception_chain(exc):
        if isinstance(current, Exception):
            root = current
    return root


def _iter_exception_chain(exc: Exception):
    """Yield exception and each linked cause/context in order."""
    current: Optional[BaseException] = exc
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def format_exception_chain(exc: Exception) -> str:
    """Format the full exception chain as a string for logging.
    
    Returns a multi-line string showing the exception chain from outer to root cause:
    "ExceptionType: message
     └─ Caused by: CauseType: cause message
        └─ Caused by: RootType: root message"
    
    Args:
        exc: The exception to format
        
    Returns:
        Formatted string showing the exception chain
    """
    chain_parts = []
    for i, e in enumerate(_iter_exception_chain(exc)):
        exc_type = type(e).__name__
        exc_msg = str(e)
        if i == 0:
            chain_parts.append(f"{exc_type}: {exc_msg}")
        else:
            indent = "   " * i
            chain_parts.append(f"{indent}└─ Caused by: {exc_type}: {exc_msg}")
    return "\n".join(chain_parts)


def _classify_aiohttp_transport_error(exc: Exception) -> Optional[str]:
    """Return TRANSIENT if exc is an aiohttp transport-layer error.
    
    Uses module-level cached import for efficiency.
    """
    if not AIOHTTP_AVAILABLE:
        return None

    if isinstance(
        exc,
        (
            aiohttp.ClientConnectionError,
            aiohttp.ServerDisconnectedError,
            aiohttp.ServerTimeoutError,
            aiohttp.ClientPayloadError,
        ),
    ):
        return MessageErrorType.TRANSIENT
    return None


class MessageErrorClassifier:
    """Classifies message processing errors to determine retry behavior.

    Classification is based on:
    1. HTTP status codes (for API errors)
    2. Exception type

    TERMINAL errors:
    - Content/parsing failures that will always fail on retry
    - Client errors (4xx except 408/429)
    - Authentication/authorization failures
    - Invalid data format errors

    TRANSIENT errors:
    - Network/connection failures
    - Timeouts
    - Rate limits (429)
    - Server errors (5xx)
    - Service unavailability
    """

    @staticmethod
    def classify_by_http_status(status_code: int) -> str:
        """Classify based on HTTP status code.

        Args:
            status_code: HTTP status code

        Returns:
            MessageErrorType constant
        """
        if _is_retryable_status(status_code):
            return MessageErrorType.TRANSIENT
        return MessageErrorType.TERMINAL

    @staticmethod
    def classify_by_exception(exc: Exception) -> str:
        """Classify based on exception type and embedded HTTP status codes.

        Args:
            exc: The exception raised during message processing

        Returns:
            MessageErrorType constant indicating how to handle the error

        Classification rules:
        0. Walk exception chain to find root cause (handles wrapped exceptions)
        1. Extract HTTP status code if available and classify by status
        2. JSON decode errors = TERMINAL (bad message format)
        3. Pydantic ValidationError = TERMINAL (invalid schema)
        4. Subprocess errors (CalledProcessError, TimeoutExpired) = TERMINAL
        5. FileNotFoundError = TERMINAL (missing dependency/file)
        6. DocumentProcessingError = TERMINAL (content parsing failed)
        7. ChunkingError = TERMINAL (content chunking failed)
        8. ExtractionError = TERMINAL (content extraction failed)
        9. ProcessingError = TERMINAL (generic processing failure)
        10. BlockContainerValidationError = TERMINAL (validation failure)
        11. Timeout/network errors = TRANSIENT (infrastructure issue)
        12. OpenAI API errors = check type (rate limit, connection = TRANSIENT)
        13. aiohttp/httpx transport errors = TRANSIENT
        14. AWS credential errors = TERMINAL
        15. Other IndexingError = TRANSIENT (may be infra-related)
        16. Unknown errors = TRANSIENT (safe default for retry)
        """
        # 0. Walk the exception chain to find the root cause
        # This handles cases where exceptions are wrapped (e.g., `raise Exception(...) from e`)
        root_exc = _get_root_cause(exc)
        chain = list(_iter_exception_chain(exc))

        # 0b. Scan full chain for aiohttp transport errors before HTTP status rules.
        # ClientPayloadError may wrap TransferEncodingError whose .code is not HTTP status.
        for chain_exc in chain:
            aiohttp_result = _classify_aiohttp_transport_error(chain_exc)
            if aiohttp_result is not None:
                return aiohttp_result

        # 0c. Scan full chain for terminal exception types before HTTP status rules.
        # Processing failures must win over incidental infrastructure errors deeper in the chain.
        # A wrapped DocumentProcessingError must be caught before an ApiCallError 500.
        for chain_exc in chain:
            # Subprocess errors (lazy import)
            try:
                import subprocess
                if isinstance(chain_exc, (subprocess.CalledProcessError, subprocess.TimeoutExpired)):
                    return MessageErrorType.TERMINAL
            except ImportError:
                pass

            # Terminal IndexingError subtypes and import/file errors
            if isinstance(
                chain_exc,
                (
                    DocumentProcessingError,
                    ChunkingError,
                    ExtractionError,
                    ProcessingError,
                    BlockContainerValidationError,
                    ImportError,
                    ModuleNotFoundError,
                    FileNotFoundError,
                ),
            ):
                return MessageErrorType.TERMINAL

            # Pydantic ValidationError in chain
            try:
                from pydantic import ValidationError as PydanticValidationError
                if isinstance(chain_exc, PydanticValidationError):
                    return MessageErrorType.TERMINAL
            except ImportError:
                pass

            # JSON decode errors in chain
            if isinstance(chain_exc, json.JSONDecodeError):
                return MessageErrorType.TERMINAL

        # 1. Check for HTTP status code in exception
        status_code = _extract_status_code(root_exc)
        if status_code is not None:
            return MessageErrorClassifier.classify_by_http_status(status_code)

        # 2. JSON parsing errors = terminal (bad message format)
        if isinstance(root_exc, json.JSONDecodeError):
            return MessageErrorType.TERMINAL

        # 3. Pydantic validation errors = terminal (message doesn't match schema)
        try:
            from pydantic import ValidationError as PydanticValidationError

            if isinstance(root_exc, PydanticValidationError):
                return MessageErrorType.TERMINAL
        except ImportError:
            pass

        # 4-5. Subprocess errors and missing files = terminal (missing dependency/config)
        try:
            import subprocess

            if isinstance(root_exc, (subprocess.CalledProcessError, subprocess.TimeoutExpired)):
                return MessageErrorType.TERMINAL
        except ImportError:
            pass

        if isinstance(root_exc, FileNotFoundError):
            return MessageErrorType.TERMINAL

        # 6-10. Processing/content-based errors = terminal (will always fail on retry)
        # These are checked BEFORE the generic IndexingError check
        if isinstance(
            root_exc,
            (
                DocumentProcessingError,
                ChunkingError,
                ExtractionError,
                ProcessingError,
                BlockContainerValidationError,
            ),
        ):
            return MessageErrorType.TERMINAL

        # 11. Timeout/network = transient (infrastructure)
        if isinstance(root_exc, (TimeoutError, asyncio.TimeoutError, ConnectionError, OSError)):
            return MessageErrorType.TRANSIENT

        # 12. OpenAI API errors
        try:
            import openai

            # Rate limits and connection errors are transient
            if isinstance(
                root_exc,
                (
                    openai.APIConnectionError,
                    openai.APITimeoutError,
                    openai.RateLimitError,
                ),
            ):
                return MessageErrorType.TRANSIENT

            # Auth errors are terminal (config issue)
            if isinstance(root_exc, openai.AuthenticationError):
                return MessageErrorType.TERMINAL

            # Other API status errors - check the status code
            if isinstance(root_exc, openai.APIStatusError):
                return MessageErrorClassifier.classify_by_http_status(root_exc.status_code)
        except ImportError:
            pass

        # 13. aiohttp ClientResponseError has status code
        try:
            import aiohttp

            if isinstance(root_exc, aiohttp.ClientResponseError):
                return MessageErrorClassifier.classify_by_http_status(root_exc.status)
        except ImportError:
            pass

        # 13b. httpx transport errors = transient
        try:
            import httpx

            if isinstance(
                root_exc,
                (
                    httpx.ConnectError,
                    httpx.TimeoutException,
                    httpx.ReadError,
                    httpx.WriteError,
                    httpx.RemoteProtocolError,
                    httpx.PoolTimeout,
                ),
            ):
                return MessageErrorType.TRANSIENT

            # httpx HTTPStatusError has response with status_code
            if isinstance(root_exc, httpx.HTTPStatusError):
                return MessageErrorClassifier.classify_by_http_status(
                    root_exc.response.status_code
                )
        except ImportError:
            pass

        # 14. AWS/boto3 errors
        try:
            from botocore.exceptions import (
                ClientError,
                ConnectionError as BotoConnectionError,
                EndpointConnectionError,
                NoCredentialsError,
            )

            # Credential errors are terminal (config issue)
            if isinstance(root_exc, NoCredentialsError):
                return MessageErrorType.TERMINAL

            # Connection errors are transient
            if isinstance(root_exc, (BotoConnectionError, EndpointConnectionError)):
                return MessageErrorType.TRANSIENT

            # ClientError - check the HTTP status code
            if isinstance(root_exc, ClientError):
                response = getattr(root_exc, "response", {})
                if isinstance(response, dict):
                    metadata = response.get("ResponseMetadata", {})
                    http_status = metadata.get("HTTPStatusCode")
                    if http_status:
                        return MessageErrorClassifier.classify_by_http_status(http_status)
                # Default boto ClientError to transient
                return MessageErrorType.TRANSIENT
        except ImportError:
            pass

        # 15. Other IndexingError subtypes (EmbeddingError, VectorStoreError, etc.)
        # are treated as transient since they may be infra-related
        if isinstance(root_exc, IndexingError):
            return MessageErrorType.TRANSIENT

        # 16. Default: assume transient (safer to retry than to drop)
        return MessageErrorType.TRANSIENT
