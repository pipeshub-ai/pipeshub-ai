"""Unit tests for message error classifier."""
import asyncio
import json
from unittest.mock import MagicMock

import pytest

from app.exceptions.indexing_exceptions import (
    BlockContainerValidationError,
    ChunkingError,
    DocumentProcessingError,
    EmbeddingError,
    ExtractionError,
    IndexingError,
    ProcessingError,
    VectorStoreError,
)
from app.services.messaging.error_classifier import (
    MessageErrorClassifier,
    MessageErrorType,
    _classify_aiohttp_transport_error,
    _extract_status_code,
    _get_root_cause,
    _is_retryable_status,
    _iter_exception_chain,
    format_exception_chain,
)


class TestHttpStatusCodeClassification:
    """Test HTTP status code classification."""

    def test_classify_by_http_status_non_retryable_400(self):
        """Test that 400 Bad Request is terminal."""
        result = MessageErrorClassifier.classify_by_http_status(400)
        assert result == MessageErrorType.TERMINAL

    def test_classify_by_http_status_non_retryable_401(self):
        """Test that 401 Unauthorized is terminal."""
        result = MessageErrorClassifier.classify_by_http_status(401)
        assert result == MessageErrorType.TERMINAL

    def test_classify_by_http_status_non_retryable_403(self):
        """Test that 403 Forbidden is terminal."""
        result = MessageErrorClassifier.classify_by_http_status(403)
        assert result == MessageErrorType.TERMINAL

    def test_classify_by_http_status_non_retryable_404(self):
        """Test that 404 Not Found is terminal."""
        result = MessageErrorClassifier.classify_by_http_status(404)
        assert result == MessageErrorType.TERMINAL

    def test_classify_by_http_status_non_retryable_413(self):
        """Test that 413 Payload Too Large is terminal."""
        result = MessageErrorClassifier.classify_by_http_status(413)
        assert result == MessageErrorType.TERMINAL

    def test_classify_by_http_status_non_retryable_422(self):
        """Test that 422 Unprocessable Entity is terminal."""
        result = MessageErrorClassifier.classify_by_http_status(422)
        assert result == MessageErrorType.TERMINAL

    def test_classify_by_http_status_retryable_408(self):
        """Test that 408 Request Timeout is transient."""
        result = MessageErrorClassifier.classify_by_http_status(408)
        assert result == MessageErrorType.TRANSIENT

    def test_classify_by_http_status_retryable_429(self):
        """Test that 429 Too Many Requests is transient."""
        result = MessageErrorClassifier.classify_by_http_status(429)
        assert result == MessageErrorType.TRANSIENT

    def test_classify_by_http_status_retryable_500(self):
        """Test that 500 Internal Server Error is transient."""
        result = MessageErrorClassifier.classify_by_http_status(500)
        assert result == MessageErrorType.TRANSIENT

    def test_classify_by_http_status_retryable_502(self):
        """Test that 502 Bad Gateway is transient."""
        result = MessageErrorClassifier.classify_by_http_status(502)
        assert result == MessageErrorType.TRANSIENT

    def test_classify_by_http_status_retryable_503(self):
        """Test that 503 Service Unavailable is transient."""
        result = MessageErrorClassifier.classify_by_http_status(503)
        assert result == MessageErrorType.TRANSIENT

    def test_classify_by_http_status_retryable_504(self):
        """Test that 504 Gateway Timeout is transient."""
        result = MessageErrorClassifier.classify_by_http_status(504)
        assert result == MessageErrorType.TRANSIENT

    def test_classify_by_http_status_retryable_520(self):
        """Test that 520 Cloudflare error is transient."""
        result = MessageErrorClassifier.classify_by_http_status(520)
        assert result == MessageErrorType.TRANSIENT

    def test_classify_by_http_status_unknown_5xx(self):
        """Test that unknown 5xx errors are transient."""
        result = MessageErrorClassifier.classify_by_http_status(599)
        assert result == MessageErrorType.TRANSIENT

    def test_is_retryable_status_function(self):
        """Test the _is_retryable_status helper function."""
        assert _is_retryable_status(429) is True
        assert _is_retryable_status(503) is True
        assert _is_retryable_status(400) is False
        assert _is_retryable_status(404) is False


class TestStatusCodeExtraction:
    """Test HTTP status code extraction from various exception types."""

    def test_extract_status_code_from_status_code_attr(self):
        """Test extraction from exception with status_code attribute."""
        exc = Exception("API error")
        exc.status_code = 503
        assert _extract_status_code(exc) == 503

    def test_extract_status_code_from_status_attr(self):
        """Test extraction from exception with status attribute."""
        exc = Exception("API error")
        exc.status = 429
        assert _extract_status_code(exc) == 429

    def test_extract_status_code_from_response_status_code(self):
        """Test extraction from exception with response.status_code (httpx style)."""
        exc = Exception("HTTP error")
        exc.response = MagicMock()
        exc.response.status_code = 502
        assert _extract_status_code(exc) == 502

    def test_extract_status_code_from_code_attr(self):
        """Test extraction from aiohttp ClientResponseError status."""
        try:
            import aiohttp

            exc = aiohttp.ClientResponseError(
                request_info=MagicMock(),
                history=(),
                status=504,
                message="Gateway Timeout",
            )
            assert _extract_status_code(exc) == 504
        except ImportError:
            pytest.skip("aiohttp not available")

    def test_extract_status_code_from_boto_response(self):
        """Test extraction from boto3-style response dict."""
        exc = Exception("AWS error")
        exc.response = {
            "Error": {"Code": "ServiceUnavailable"},
            "ResponseMetadata": {"HTTPStatusCode": 503},
        }
        assert _extract_status_code(exc) == 503

    def test_extract_status_code_returns_none_when_missing(self):
        """Test that None is returned when no status code is available."""
        exc = Exception("Generic error")
        assert _extract_status_code(exc) is None


class TestExceptionWithStatusCode:
    """Test exceptions that contain HTTP status codes."""

    def test_exception_with_retryable_status_code(self):
        """Test exception with retryable status code is transient."""
        exc = Exception("Service unavailable")
        exc.status_code = 503
        result = MessageErrorClassifier.classify_by_exception(exc)
        assert result == MessageErrorType.TRANSIENT

    def test_exception_with_non_retryable_status_code(self):
        """Test exception with non-retryable status code is terminal."""
        exc = Exception("Not found")
        exc.status_code = 404
        result = MessageErrorClassifier.classify_by_exception(exc)
        assert result == MessageErrorType.TERMINAL

    def test_exception_with_rate_limit_status_code(self):
        """Test exception with 429 rate limit status code is transient."""
        exc = Exception("Rate limited")
        exc.status_code = 429
        result = MessageErrorClassifier.classify_by_exception(exc)
        assert result == MessageErrorType.TRANSIENT


class TestMessageErrorClassifier:
    """Test suite for MessageErrorClassifier.
    
    Classification is based on:
    1. HTTP status codes in exceptions
    2. Exception type
    """

    def test_classify_by_exception_json_decode_error(self):
        """Test that JSON decode errors are classified as terminal."""
        exc = json.JSONDecodeError("Bad JSON", "", 0)
        result = MessageErrorClassifier.classify_by_exception(exc)
        assert result == MessageErrorType.TERMINAL

    def test_classify_by_exception_pydantic_validation_error(self):
        """Test that Pydantic validation errors are classified as terminal."""
        try:
            from pydantic import BaseModel, ValidationError

            class TestModel(BaseModel):
                required_field: str

            try:
                TestModel(wrong_field="value")
            except ValidationError as e:
                result = MessageErrorClassifier.classify_by_exception(e)
                assert result == MessageErrorType.TERMINAL
        except ImportError:
            pytest.skip("Pydantic not available")

    def test_classify_by_exception_document_processing_error(self):
        """Test that DocumentProcessingError is classified as terminal."""
        exc = DocumentProcessingError("Failed to process document")
        result = MessageErrorClassifier.classify_by_exception(exc)
        assert result == MessageErrorType.TERMINAL

    def test_classify_by_exception_chunking_error(self):
        """Test that ChunkingError is classified as terminal."""
        exc = ChunkingError("Chunking failed")
        result = MessageErrorClassifier.classify_by_exception(exc)
        assert result == MessageErrorType.TERMINAL

    def test_classify_by_exception_extraction_error(self):
        """Test that ExtractionError is classified as terminal."""
        exc = ExtractionError("Extraction failed")
        result = MessageErrorClassifier.classify_by_exception(exc)
        assert result == MessageErrorType.TERMINAL

    def test_classify_by_exception_processing_error(self):
        """Test that ProcessingError is classified as terminal."""
        exc = ProcessingError("Processing failed")
        result = MessageErrorClassifier.classify_by_exception(exc)
        assert result == MessageErrorType.TERMINAL

    def test_classify_by_exception_block_container_validation_error(self):
        """Test that BlockContainerValidationError is classified as terminal."""
        mock_error = MagicMock()
        mock_error.location = "test"
        mock_error.code = "TEST"
        mock_error.message = "Test error"
        exc = BlockContainerValidationError([mock_error])
        result = MessageErrorClassifier.classify_by_exception(exc)
        assert result == MessageErrorType.TERMINAL

    def test_classify_by_exception_timeout_error(self):
        """Test that timeout errors are classified as transient."""
        exc = TimeoutError("Request timeout")
        result = MessageErrorClassifier.classify_by_exception(exc)
        assert result == MessageErrorType.TRANSIENT

    def test_classify_by_exception_asyncio_timeout(self):
        """Test that asyncio timeout errors are classified as transient."""
        exc = asyncio.TimeoutError()
        result = MessageErrorClassifier.classify_by_exception(exc)
        assert result == MessageErrorType.TRANSIENT

    def test_classify_by_exception_connection_error(self):
        """Test that connection errors are classified as transient."""
        exc = ConnectionError("Connection refused")
        result = MessageErrorClassifier.classify_by_exception(exc)
        assert result == MessageErrorType.TRANSIENT

    def test_classify_by_exception_os_error(self):
        """Test that OS errors are classified as transient."""
        exc = OSError("Network unreachable")
        result = MessageErrorClassifier.classify_by_exception(exc)
        assert result == MessageErrorType.TRANSIENT

    def test_classify_by_exception_indexing_error_base(self):
        """Test that base IndexingError is classified as transient."""
        exc = IndexingError("Processing failed", record_id="record_123")
        result = MessageErrorClassifier.classify_by_exception(exc)
        assert result == MessageErrorType.TRANSIENT

    def test_classify_by_exception_embedding_error(self):
        """Test that EmbeddingError is classified as transient."""
        exc = EmbeddingError("Embedding failed")
        result = MessageErrorClassifier.classify_by_exception(exc)
        assert result == MessageErrorType.TRANSIENT

    def test_classify_by_exception_vector_store_error(self):
        """Test that VectorStoreError is classified as transient."""
        exc = VectorStoreError("Qdrant connection failed")
        result = MessageErrorClassifier.classify_by_exception(exc)
        assert result == MessageErrorType.TRANSIENT

    def test_classify_by_exception_unknown_error(self):
        """Test that unknown errors default to transient."""
        exc = RuntimeError("Unknown error")
        result = MessageErrorClassifier.classify_by_exception(exc)
        assert result == MessageErrorType.TRANSIENT


class TestOpenAIErrors:
    """Test classification of OpenAI API errors."""

    def test_openai_api_connection_error(self):
        """Test that OpenAI connection errors are transient."""
        try:
            import openai

            exc = openai.APIConnectionError(request=MagicMock())
            result = MessageErrorClassifier.classify_by_exception(exc)
            assert result == MessageErrorType.TRANSIENT
        except ImportError:
            pytest.skip("OpenAI not available")

    def test_openai_api_timeout_error(self):
        """Test that OpenAI timeout errors are transient."""
        try:
            import openai

            exc = openai.APITimeoutError(request=MagicMock())
            result = MessageErrorClassifier.classify_by_exception(exc)
            assert result == MessageErrorType.TRANSIENT
        except ImportError:
            pytest.skip("OpenAI not available")

    def test_openai_rate_limit_error(self):
        """Test that OpenAI rate limit errors are transient."""
        try:
            import openai

            mock_response = MagicMock()
            mock_response.status_code = 429
            exc = openai.RateLimitError(
                message="Rate limit exceeded",
                response=mock_response,
                body=None,
            )
            result = MessageErrorClassifier.classify_by_exception(exc)
            assert result == MessageErrorType.TRANSIENT
        except ImportError:
            pytest.skip("OpenAI not available")

    def test_openai_auth_error(self):
        """Test that OpenAI auth errors are terminal."""
        try:
            import openai

            mock_response = MagicMock()
            mock_response.status_code = 401
            exc = openai.AuthenticationError(
                message="Invalid API key",
                response=mock_response,
                body=None,
            )
            result = MessageErrorClassifier.classify_by_exception(exc)
            assert result == MessageErrorType.TERMINAL
        except ImportError:
            pytest.skip("OpenAI not available")


class TestAiohttpErrors:
    """Test classification of aiohttp errors."""

    def test_aiohttp_client_connection_error(self):
        """Test that aiohttp connection errors are transient."""
        try:
            import aiohttp

            exc = aiohttp.ClientConnectionError("Connection refused")
            result = MessageErrorClassifier.classify_by_exception(exc)
            assert result == MessageErrorType.TRANSIENT
        except ImportError:
            pytest.skip("aiohttp not available")

    def test_aiohttp_server_disconnected_error(self):
        """Test that aiohttp server disconnect errors are transient."""
        try:
            import aiohttp

            exc = aiohttp.ServerDisconnectedError("Server disconnected")
            result = MessageErrorClassifier.classify_by_exception(exc)
            assert result == MessageErrorType.TRANSIENT
        except ImportError:
            pytest.skip("aiohttp not available")

    def test_aiohttp_client_response_error_retryable(self):
        """Test that aiohttp response errors with retryable status are transient."""
        try:
            import aiohttp

            exc = aiohttp.ClientResponseError(
                request_info=MagicMock(),
                history=(),
                status=503,
                message="Service Unavailable",
            )
            result = MessageErrorClassifier.classify_by_exception(exc)
            assert result == MessageErrorType.TRANSIENT
        except ImportError:
            pytest.skip("aiohttp not available")

    def test_aiohttp_client_response_error_non_retryable(self):
        """Test that aiohttp response errors with non-retryable status are terminal."""
        try:
            import aiohttp

            exc = aiohttp.ClientResponseError(
                request_info=MagicMock(),
                history=(),
                status=404,
                message="Not Found",
            )
            result = MessageErrorClassifier.classify_by_exception(exc)
            assert result == MessageErrorType.TERMINAL
        except ImportError:
            pytest.skip("aiohttp not available")


class TestHttpxErrors:
    """Test classification of httpx errors."""

    def test_httpx_connect_error(self):
        """Test that httpx connection errors are transient."""
        try:
            import httpx

            exc = httpx.ConnectError("Connection refused")
            result = MessageErrorClassifier.classify_by_exception(exc)
            assert result == MessageErrorType.TRANSIENT
        except ImportError:
            pytest.skip("httpx not available")

    def test_httpx_timeout_error(self):
        """Test that httpx timeout errors are transient."""
        try:
            import httpx

            exc = httpx.TimeoutException("Request timed out")
            result = MessageErrorClassifier.classify_by_exception(exc)
            assert result == MessageErrorType.TRANSIENT
        except ImportError:
            pytest.skip("httpx not available")

    def test_httpx_read_error(self):
        """Test that httpx read errors are transient."""
        try:
            import httpx

            exc = httpx.ReadError("Read failed")
            result = MessageErrorClassifier.classify_by_exception(exc)
            assert result == MessageErrorType.TRANSIENT
        except ImportError:
            pytest.skip("httpx not available")

    def test_httpx_http_status_error_retryable(self):
        """Test that httpx status errors with retryable status are transient."""
        try:
            import httpx

            mock_request = MagicMock()
            mock_response = MagicMock()
            mock_response.status_code = 502
            exc = httpx.HTTPStatusError(
                "Bad Gateway",
                request=mock_request,
                response=mock_response,
            )
            result = MessageErrorClassifier.classify_by_exception(exc)
            assert result == MessageErrorType.TRANSIENT
        except ImportError:
            pytest.skip("httpx not available")

    def test_httpx_http_status_error_non_retryable(self):
        """Test that httpx status errors with non-retryable status are terminal."""
        try:
            import httpx

            mock_request = MagicMock()
            mock_response = MagicMock()
            mock_response.status_code = 400
            exc = httpx.HTTPStatusError(
                "Bad Request",
                request=mock_request,
                response=mock_response,
            )
            result = MessageErrorClassifier.classify_by_exception(exc)
            assert result == MessageErrorType.TERMINAL
        except ImportError:
            pytest.skip("httpx not available")


class TestBoto3Errors:
    """Test classification of boto3/botocore errors."""

    def test_boto_no_credentials_error(self):
        """Test that boto NoCredentialsError is terminal."""
        try:
            from botocore.exceptions import NoCredentialsError

            exc = NoCredentialsError()
            result = MessageErrorClassifier.classify_by_exception(exc)
            assert result == MessageErrorType.TERMINAL
        except ImportError:
            pytest.skip("botocore not available")

    def test_boto_endpoint_connection_error(self):
        """Test that boto endpoint connection errors are transient."""
        try:
            from botocore.exceptions import EndpointConnectionError

            exc = EndpointConnectionError(endpoint_url="https://example.com")
            result = MessageErrorClassifier.classify_by_exception(exc)
            assert result == MessageErrorType.TRANSIENT
        except ImportError:
            pytest.skip("botocore not available")

    def test_boto_client_error_with_http_status(self):
        """Test boto ClientError classification based on HTTP status."""
        try:
            from botocore.exceptions import ClientError

            exc = ClientError(
                error_response={
                    "Error": {"Code": "ServiceUnavailable"},
                    "ResponseMetadata": {"HTTPStatusCode": 503},
                },
                operation_name="PutObject",
            )
            result = MessageErrorClassifier.classify_by_exception(exc)
            assert result == MessageErrorType.TRANSIENT
        except ImportError:
            pytest.skip("botocore not available")


class TestRootCauseExtraction:
    """Test the _get_root_cause helper function."""

    def test_get_root_cause_simple_exception(self):
        """Test that a simple exception returns itself."""
        exc = ValueError("test error")
        root = _get_root_cause(exc)
        assert root is exc

    def test_get_root_cause_walks_cause_chain(self):
        """Test that __cause__ chain is walked correctly."""
        original = ValueError("original")
        wrapped = RuntimeError("wrapped")
        wrapped.__cause__ = original
        
        root = _get_root_cause(wrapped)
        assert root is original

    def test_get_root_cause_multiple_levels(self):
        """Test that multiple levels of __cause__ are walked."""
        deepest = ValueError("deepest")
        middle = RuntimeError("middle")
        middle.__cause__ = deepest
        outer = Exception("outer")
        outer.__cause__ = middle
        
        root = _get_root_cause(outer)
        assert root is deepest

    def test_get_root_cause_walks_context_chain(self):
        """Test that __context__ chain is walked when __cause__ is absent."""
        original = ValueError("original")
        wrapped = RuntimeError("wrapped")
        wrapped.__context__ = original
        
        root = _get_root_cause(wrapped)
        assert root is original

    def test_get_root_cause_prefers_cause_over_context(self):
        """Test that __cause__ is preferred over __context__."""
        cause_exc = ValueError("cause")
        context_exc = RuntimeError("context")
        wrapped = Exception("wrapped")
        wrapped.__cause__ = cause_exc
        wrapped.__context__ = context_exc
        
        root = _get_root_cause(wrapped)
        assert root is cause_exc

    def test_get_root_cause_handles_circular_reference(self):
        """Test that circular references don't cause infinite loops."""
        exc1 = ValueError("exc1")
        exc2 = RuntimeError("exc2")
        exc1.__cause__ = exc2
        exc2.__cause__ = exc1  # Circular reference
        
        # Should not hang and should return one of them
        root = _get_root_cause(exc1)
        assert root in (exc1, exc2)


class TestSubprocessErrors:
    """Test classification of subprocess errors."""

    def test_subprocess_called_process_error_terminal(self):
        """Test that subprocess.CalledProcessError is terminal."""
        import subprocess

        exc = subprocess.CalledProcessError(
            returncode=127,
            cmd=["libreoffice", "--convert-to", "docx"],
            output=b"command not found",
        )
        result = MessageErrorClassifier.classify_by_exception(exc)
        assert result == MessageErrorType.TERMINAL

    def test_subprocess_timeout_expired_terminal(self):
        """Test that subprocess.TimeoutExpired is terminal."""
        import subprocess

        exc = subprocess.TimeoutExpired(
            cmd=["long_running_command"],
            timeout=30,
        )
        result = MessageErrorClassifier.classify_by_exception(exc)
        assert result == MessageErrorType.TERMINAL

    def test_file_not_found_error_terminal(self):
        """Test that FileNotFoundError is terminal."""
        exc = FileNotFoundError("No such file or directory: '/path/to/file'")
        result = MessageErrorClassifier.classify_by_exception(exc)
        assert result == MessageErrorType.TERMINAL


class TestWrappedExceptionClassification:
    """Test that wrapped exceptions are correctly classified by their root cause."""

    def test_wrapped_subprocess_error_classified_as_terminal(self):
        """Test wrapped subprocess error is classified by root cause."""
        import subprocess

        # Simulate the pattern in record.py where exceptions are wrapped
        original = subprocess.CalledProcessError(
            returncode=127,
            cmd=["libreoffice"],
            output=b"command not found",
        )
        wrapped = Exception("Processing failed")
        wrapped.__cause__ = original

        result = MessageErrorClassifier.classify_by_exception(wrapped)
        assert result == MessageErrorType.TERMINAL

    def test_wrapped_file_not_found_classified_as_terminal(self):
        """Test wrapped FileNotFoundError is classified by root cause."""
        original = FileNotFoundError("dependency.so not found")
        wrapped = Exception("Failed to load module")
        wrapped.__cause__ = original

        result = MessageErrorClassifier.classify_by_exception(wrapped)
        assert result == MessageErrorType.TERMINAL

    def test_wrapped_processing_error_classified_as_terminal(self):
        """Test wrapped DocumentProcessingError is classified by root cause."""
        original = DocumentProcessingError("Parsing failed")
        wrapped = Exception("Operation failed")
        wrapped.__cause__ = original

        result = MessageErrorClassifier.classify_by_exception(wrapped)
        assert result == MessageErrorType.TERMINAL

    def test_wrapped_timeout_error_classified_as_transient(self):
        """Test wrapped timeout error is classified by root cause."""
        original = TimeoutError("Request timed out")
        wrapped = Exception("Request failed")
        wrapped.__cause__ = original

        result = MessageErrorClassifier.classify_by_exception(wrapped)
        assert result == MessageErrorType.TRANSIENT

    def test_wrapped_connection_error_classified_as_transient(self):
        """Test wrapped connection error is classified by root cause."""
        original = ConnectionError("Connection refused")
        wrapped = Exception("Network error")
        wrapped.__cause__ = original

        result = MessageErrorClassifier.classify_by_exception(wrapped)
        assert result == MessageErrorType.TRANSIENT

    def test_deeply_nested_exception_classification(self):
        """Test classification with multiple levels of wrapping."""
        import subprocess

        # Deepest level: subprocess error (terminal)
        deepest = subprocess.CalledProcessError(
            returncode=1,
            cmd=["libreoffice"],
        )
        # Middle level: generic exception
        middle = RuntimeError("Conversion failed")
        middle.__cause__ = deepest
        # Outer level: another generic exception
        outer = Exception("Processing failed")
        outer.__cause__ = middle

        result = MessageErrorClassifier.classify_by_exception(outer)
        assert result == MessageErrorType.TERMINAL

    def test_client_payload_error_with_transfer_encoding_in_chain(self):
        """ClientPayloadError must be transient even when root is TransferEncodingError."""
        try:
            import aiohttp
            from aiohttp.http_exceptions import TransferEncodingError
        except ImportError:
            pytest.skip("aiohttp not available")

        try:
            raise TransferEncodingError(
                message="Not enough data to satisfy transfer length header."
            )
        except TransferEncodingError as te:
            try:
                raise aiohttp.ClientPayloadError(
                    "Response payload is not completed"
                ) from te
            except aiohttp.ClientPayloadError as cpe:
                wrapped = Exception(str(cpe))
                wrapped.__cause__ = cpe
                result = MessageErrorClassifier.classify_by_exception(wrapped)
                assert result == MessageErrorType.TRANSIENT


class TestChainTerminalClassification:
    """Test that terminal exceptions in chains are detected correctly."""

    def test_document_processing_error_in_deep_chain(self):
        """Test DocumentProcessingError buried under double Exception wrap is TERMINAL."""
        # Innermost: DocumentProcessingError (terminal)
        doc_error = DocumentProcessingError(
            "SVG conversion dependency missing: cairosvg is not installed"
        )
        # Middle: wrapped in generic Exception
        middle = Exception("Failed to convert SVG to PNG")
        middle.__cause__ = doc_error
        # Outer: wrapped again in generic Exception
        outer = Exception("Error processing image: Failed to convert SVG to PNG")
        outer.__cause__ = middle

        result = MessageErrorClassifier.classify_by_exception(outer)
        assert result == MessageErrorType.TERMINAL

    def test_module_not_found_error_in_chain(self):
        """Test ModuleNotFoundError in middle of chain is TERMINAL."""
        # Reproduce pre-fix SVG chain: generic Exception at root but ModuleNotFoundError in middle
        module_error = ModuleNotFoundError("No module named 'cairosvg'")
        generic = Exception("import from cairosvg failed")
        generic.__cause__ = module_error
        outer = Exception("SVG to PNG conversion failed")
        outer.__cause__ = generic

        result = MessageErrorClassifier.classify_by_exception(outer)
        assert result == MessageErrorType.TERMINAL

    def test_import_error_in_chain(self):
        """Test ImportError in chain is TERMINAL."""
        import_error = ImportError("cannot import name 'svg2png' from 'cairosvg'")
        wrapper = Exception("Dependency load failed")
        wrapper.__cause__ = import_error

        result = MessageErrorClassifier.classify_by_exception(wrapper)
        assert result == MessageErrorType.TERMINAL

    def test_processing_error_before_api_error_500(self):
        """Test DocumentProcessingError in chain before ApiCallError 500 is TERMINAL."""
        # This tests ordering: processing failures must win over infrastructure errors
        # Create an API error with 500 status (transient)
        api_error = Exception("API call failed")
        api_error.status_code = 500

        # Wrap it in DocumentProcessingError (terminal)
        doc_error = DocumentProcessingError(
            "Failed to parse document",
            details={"error": str(api_error)},
        )
        doc_error.__cause__ = api_error

        # The classifier should find DocumentProcessingError in the chain first
        result = MessageErrorClassifier.classify_by_exception(doc_error)
        assert result == MessageErrorType.TERMINAL

    def test_subprocess_called_process_error_in_chain(self):
        """Test subprocess.CalledProcessError in chain is TERMINAL."""
        import subprocess

        # LibreOffice conversion failure
        subprocess_error = subprocess.CalledProcessError(
            returncode=127,
            cmd=["libreoffice", "--convert-to", "docx"],
        )
        wrapper = Exception("Document conversion failed")
        wrapper.__cause__ = subprocess_error

        result = MessageErrorClassifier.classify_by_exception(wrapper)
        assert result == MessageErrorType.TERMINAL

    def test_pydantic_validation_error_in_chain(self):
        """Test Pydantic ValidationError in chain is TERMINAL."""
        try:
            from pydantic import BaseModel, ValidationError

            class TestModel(BaseModel):
                required_field: str

            try:
                TestModel(wrong_field="value")
            except ValidationError as val_error:
                wrapper = Exception("Message validation failed")
                wrapper.__cause__ = val_error

                result = MessageErrorClassifier.classify_by_exception(wrapper)
                assert result == MessageErrorType.TERMINAL
        except ImportError:
            pytest.skip("Pydantic not available")

    def test_json_decode_error_in_chain(self):
        """Test JSONDecodeError in chain is TERMINAL."""
        json_error = json.JSONDecodeError("Expecting value", "", 0)
        wrapper = Exception("Failed to parse response")
        wrapper.__cause__ = json_error

        result = MessageErrorClassifier.classify_by_exception(wrapper)
        assert result == MessageErrorType.TERMINAL


class TestMessageErrorType:
    """Tests for MessageErrorType constants."""

    def test_error_types_exist(self):
        """Test that expected error types exist."""
        assert MessageErrorType.TERMINAL == "terminal"
        assert MessageErrorType.TRANSIENT == "transient"

    def test_no_handled_type(self):
        """Test that HANDLED type no longer exists."""
        assert not hasattr(MessageErrorType, "HANDLED")


class TestIterExceptionChain:
    """Test the _iter_exception_chain utility function."""

    def test_iter_simple_exception(self):
        """Test that a simple exception yields itself."""
        exc = ValueError("test error")
        chain = list(_iter_exception_chain(exc))
        assert len(chain) == 1
        assert chain[0] is exc

    def test_iter_exception_with_cause(self):
        """Test that __cause__ chain is iterated correctly."""
        original = ValueError("original")
        wrapped = RuntimeError("wrapped")
        wrapped.__cause__ = original

        chain = list(_iter_exception_chain(wrapped))
        assert len(chain) == 2
        assert chain[0] is wrapped
        assert chain[1] is original

    def test_iter_exception_with_context(self):
        """Test that __context__ chain is iterated when __cause__ is absent."""
        original = ValueError("original")
        wrapped = RuntimeError("wrapped")
        wrapped.__context__ = original

        chain = list(_iter_exception_chain(wrapped))
        assert len(chain) == 2
        assert chain[0] is wrapped
        assert chain[1] is original

    def test_iter_multiple_levels(self):
        """Test iteration with multiple levels of exception chaining."""
        deepest = ValueError("deepest")
        middle = RuntimeError("middle")
        middle.__cause__ = deepest
        outer = Exception("outer")
        outer.__cause__ = middle

        chain = list(_iter_exception_chain(outer))
        assert len(chain) == 3
        assert chain[0] is outer
        assert chain[1] is middle
        assert chain[2] is deepest

    def test_iter_prefers_cause_over_context(self):
        """Test that __cause__ is preferred over __context__."""
        cause_exc = ValueError("cause")
        context_exc = RuntimeError("context")
        wrapped = Exception("wrapped")
        wrapped.__cause__ = cause_exc
        wrapped.__context__ = context_exc

        chain = list(_iter_exception_chain(wrapped))
        assert len(chain) == 2
        assert chain[0] is wrapped
        assert chain[1] is cause_exc

    def test_iter_handles_circular_reference(self):
        """Test that circular references don't cause infinite loops."""
        exc1 = ValueError("exc1")
        exc2 = RuntimeError("exc2")
        exc1.__cause__ = exc2
        exc2.__cause__ = exc1

        chain = list(_iter_exception_chain(exc1))
        assert len(chain) == 2
        assert chain[0] is exc1
        assert chain[1] is exc2


class TestFormatExceptionChain:
    """Test the format_exception_chain utility function."""

    def test_format_single_exception(self):
        """Test formatting a simple exception."""
        exc = ValueError("test error")
        formatted = format_exception_chain(exc)
        assert "ValueError: test error" in formatted
        assert "Caused by" not in formatted

    def test_format_two_level_chain(self):
        """Test formatting a two-level exception chain."""
        original = ValueError("original error")
        wrapped = RuntimeError("wrapped error")
        wrapped.__cause__ = original

        formatted = format_exception_chain(wrapped)
        assert "RuntimeError: wrapped error" in formatted
        assert "Caused by: ValueError: original error" in formatted

    def test_format_three_level_chain(self):
        """Test formatting a three-level exception chain."""
        deepest = ValueError("deepest")
        middle = RuntimeError("middle")
        middle.__cause__ = deepest
        outer = Exception("outer")
        outer.__cause__ = middle

        formatted = format_exception_chain(outer)
        lines = formatted.split("\n")
        assert len(lines) == 3
        assert "Exception: outer" in lines[0]
        assert "Caused by: RuntimeError: middle" in lines[1]
        assert "Caused by: ValueError: deepest" in lines[2]

    def test_format_indentation(self):
        """Test that nested exceptions have proper indentation."""
        original = ValueError("original")
        wrapped = RuntimeError("wrapped")
        wrapped.__cause__ = original

        formatted = format_exception_chain(wrapped)
        lines = formatted.split("\n")
        assert not lines[0].startswith(" ")
        assert lines[1].startswith("   ")

    def test_format_with_context(self):
        """Test formatting when using __context__ instead of __cause__."""
        original = ValueError("original")
        wrapped = RuntimeError("wrapped")
        wrapped.__context__ = original

        formatted = format_exception_chain(wrapped)
        assert "RuntimeError: wrapped" in formatted
        assert "Caused by: ValueError: original" in formatted


class TestClassifyAiohttpTransportError:
    """Test the _classify_aiohttp_transport_error utility function."""

    def test_client_connection_error_is_transient(self):
        """Test that ClientConnectionError is classified as transient."""
        try:
            import aiohttp

            exc = aiohttp.ClientConnectionError("Connection refused")
            result = _classify_aiohttp_transport_error(exc)
            assert result == MessageErrorType.TRANSIENT
        except ImportError:
            pytest.skip("aiohttp not available")

    def test_server_disconnected_error_is_transient(self):
        """Test that ServerDisconnectedError is classified as transient."""
        try:
            import aiohttp

            exc = aiohttp.ServerDisconnectedError("Server disconnected")
            result = _classify_aiohttp_transport_error(exc)
            assert result == MessageErrorType.TRANSIENT
        except ImportError:
            pytest.skip("aiohttp not available")

    def test_server_timeout_error_is_transient(self):
        """Test that ServerTimeoutError is classified as transient."""
        try:
            import aiohttp

            exc = aiohttp.ServerTimeoutError("Server timeout")
            result = _classify_aiohttp_transport_error(exc)
            assert result == MessageErrorType.TRANSIENT
        except ImportError:
            pytest.skip("aiohttp not available")

    def test_client_payload_error_is_transient(self):
        """Test that ClientPayloadError is classified as transient."""
        try:
            import aiohttp

            exc = aiohttp.ClientPayloadError("Payload error")
            result = _classify_aiohttp_transport_error(exc)
            assert result == MessageErrorType.TRANSIENT
        except ImportError:
            pytest.skip("aiohttp not available")

    def test_non_transport_error_returns_none(self):
        """Test that non-transport aiohttp errors return None."""
        try:
            import aiohttp

            exc = aiohttp.ClientResponseError(
                request_info=MagicMock(),
                history=(),
                status=404,
                message="Not Found",
            )
            result = _classify_aiohttp_transport_error(exc)
            assert result is None
        except ImportError:
            pytest.skip("aiohttp not available")

    def test_non_aiohttp_error_returns_none(self):
        """Test that non-aiohttp errors return None."""
        exc = ValueError("Not an aiohttp error")
        result = _classify_aiohttp_transport_error(exc)
        assert result is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
