"""What a person sees when a file fails to index.

Each case builds the exception chain the indexer actually produces (a PipesHub
wrapper raised ``from`` the real cause) and pins the reason stored on the record.
"""

import asyncio
import re
from pathlib import Path

import httpx
import openai
import pytest

from app.exceptions.indexing_exceptions import (
    DocumentProcessingError,
    EmbeddingError,
    EmbeddingModelUnavailableError,
    EmbeddingNotConfiguredError,
    IndexingError,
    VectorStoreError,
)
from app.utils import user_errors as ue
from app.utils.llm import LLMNotConfiguredError

_REQUEST = httpx.Request("POST", "https://api.example.com/v1/embeddings")


def _wrapped(outer: BaseException, cause: BaseException) -> BaseException:
    try:
        try:
            raise cause
        except BaseException as inner:
            raise outer from inner
    except BaseException as raised:
        return raised


def _status_error(cls: type, status: int) -> BaseException:
    response = httpx.Response(status, request=_REQUEST, json={"error": {"message": "x"}})
    return cls(f"Error code: {status}", response=response, body=None)


class _QdrantDown(Exception):
    pass


_QdrantDown.__module__ = "qdrant_client.http.exceptions"


class _RedisWrongPass(Exception):
    pass


_RedisWrongPass.__module__ = "redis.exceptions"


class TestSpecificCauses:
    def test_nothing_known_gives_the_generic_reason(self) -> None:
        assert ue.to_user_reason(None) == ue.GENERIC_FAILURE
        assert ue.to_user_reason(KeyError("llm")) == ue.GENERIC_FAILURE

    def test_missing_language_model_keeps_its_own_message_even_when_wrapped(self) -> None:
        missing = LLMNotConfiguredError()
        assert ue.to_user_reason(missing) == str(missing)
        wrapped = _wrapped(DocumentProcessingError("Failed to process document: 'llm'"), missing)
        assert ue.to_user_reason(wrapped) == str(missing)

    def test_scanned_document(self) -> None:
        assert ue.to_user_reason(IndexingError(ue.SCANNED_DOCUMENT_NEEDS_OCR)) == ue.SCANNED_DOCUMENT_NEEDS_OCR

    @pytest.mark.parametrize(
        "reason",
        [
            ue.EPUB_UNREADABLE,
            ue.EPUB_COPY_PROTECTED,
            ue.EPUB_TOO_LARGE,
            ue.EPUB_UNSAFE_PATHS,
            ue.EPUB_NO_READABLE_CHAPTERS,
        ],
    )
    def test_an_epub_reason_is_stored_as_written(self, reason: str) -> None:
        from app.services.parsing.client import ParsingClientError
        from app.services.parsing.interface import ParseError, ParseErrorCode

        indexed_in_process = _wrapped(DocumentProcessingError(reason), ParseError(ParseErrorCode.PARSE_FAILED, reason))
        via_parsing_service = ParsingClientError(ParseErrorCode.PARSE_FAILED, reason)
        assert ue.to_user_reason(indexed_in_process) == reason
        assert ue.to_user_reason(via_parsing_service) == reason

    def test_a_non_text_first_argument_is_not_mistaken_for_a_reason(self) -> None:
        assert ue.to_user_reason(DocumentProcessingError({"not": "hashable"})) == ue.UNREADABLE_FILE

    def test_no_embedding_model(self) -> None:
        exc = _wrapped(
            IndexingError("Failed to get embedding model instance"),
            EmbeddingNotConfiguredError("no config and no fallback"),
        )
        assert ue.to_user_reason(exc) == ue.EMBEDDING_NOT_CONFIGURED

    def test_password_protected_file(self) -> None:
        exc = _wrapped(DocumentProcessingError("Failed to process document"), ValueError("File has not been decrypted"))
        assert ue.to_user_reason(exc) == ue.PASSWORD_PROTECTED

    def test_file_too_large(self) -> None:
        assert ue.to_user_reason(_wrapped(DocumentProcessingError("x"), MemoryError())) == ue.FILE_TOO_LARGE

    def test_text_processing_timeout_as_vectorstore_raises_it(self) -> None:
        # vectorstore.py: `except asyncio.TimeoutError: raise DocumentProcessingError(...)`,
        # so the timeout is the implicit __context__.
        async def slow() -> None:
            await asyncio.sleep(1)

        async def process() -> None:
            try:
                await asyncio.wait_for(slow(), timeout=0.001)
            except asyncio.TimeoutError:
                raise DocumentProcessingError("Text processing timed out after 600s for record r1 (4 blocks)")  # noqa: B904 - the implicit context is what is under test

        with pytest.raises(DocumentProcessingError) as caught:
            asyncio.run(process())
        assert isinstance(caught.value.__context__, TimeoutError)
        assert ue.to_user_reason(caught.value) == ue.PROCESSING_TIMED_OUT

    def test_libreoffice_timeout_as_it_raises_it(self) -> None:
        # libreoffice_convert.py: `except asyncio.TimeoutError as e: raise ... from e`.
        exc = _wrapped(
            DocumentProcessingError("LibreOffice conversion timed out after 120 seconds"),
            asyncio.TimeoutError(),
        )
        assert ue.to_user_reason(exc) == ue.PROCESSING_TIMED_OUT

    def test_unreadable_file(self) -> None:
        exc = _wrapped(DocumentProcessingError("Failed to process document"), ValueError("bad xref table"))
        assert ue.to_user_reason(exc) == ue.UNREADABLE_FILE


class TestAIProviders:
    @pytest.mark.parametrize(
        "cause",
        [
            openai.APIConnectionError(request=_REQUEST),
            openai.APITimeoutError(request=_REQUEST),
            httpx.ConnectTimeout("timed out"),
            httpx.ReadTimeout("timed out"),
            asyncio.TimeoutError(),
        ],
        ids=["connection", "sdk-timeout", "connect-timeout", "read-timeout", "asyncio-timeout"],
    )
    def test_embedding_model_not_answering(self, cause: BaseException) -> None:
        exc = _wrapped(
            EmbeddingError("Dense embedding failed after 3 attempts (60s each) for batch of 12 texts / 45000 chars (record r1)"),
            cause,
        )
        assert ue.to_user_reason(exc) == ue._ai_messages("embedding model")["timeout"]

    def test_embedding_rate_limited(self) -> None:
        exc = _wrapped(EmbeddingError("Dense embedding failed"), _status_error(openai.RateLimitError, 429))
        assert ue.to_user_reason(exc) == ue._ai_messages("embedding model")["rate_limit"]

    @pytest.mark.parametrize(
        ("cause", "code"),
        [
            (openai.APIConnectionError(request=_REQUEST), "server_error"),
            (_status_error(openai.InternalServerError, 503), "server_error"),
            (_status_error(openai.RateLimitError, 429), "rate_limit"),
            (_status_error(openai.AuthenticationError, 401), "auth_error"),
        ],
        ids=["connection", "5xx", "429", "401"],
    )
    def test_language_model_failures_while_processing(self, cause: BaseException, code: str) -> None:
        # Table summaries and image checks call the LLM inside the processor,
        # which wraps whatever it raises as a content error.
        exc = _wrapped(DocumentProcessingError("Failed to process document: Connection error."), cause)
        assert ue.to_user_reason(exc) == ue._ai_messages("AI model")[code]

    def test_openai_connection_error_wrapping_an_httpx_error(self) -> None:
        sdk = _wrapped(openai.APIConnectionError(request=_REQUEST), httpx.ConnectError("refused"))
        exc = _wrapped(DocumentProcessingError("Failed to process document"), sdk)
        assert ue.to_user_reason(exc) == ue._ai_messages("AI model")["server_error"]

    def test_embedding_model_that_fails_its_probe(self) -> None:
        exc = _wrapped(EmbeddingModelUnavailableError("Failed to get embedding model"), ValueError("dimension probe"))
        assert ue.to_user_reason(exc) == ue.EMBEDDING_UNAVAILABLE

    def test_wrapper_counts_are_not_read_as_status_codes(self) -> None:
        exc = _wrapped(
            EmbeddingError("Dense embedding failed for batch of 12 texts / 45000 chars (record r429)"),
            ValueError("unexpected embedding shape"),
        )
        assert ue.to_user_reason(exc) == ue.EMBEDDING_UNAVAILABLE


class TestRawHttpUnderContent:
    """An AI call made while reading the file, through a plain HTTP client."""

    def test_rejected_key(self) -> None:
        response = httpx.Response(401, request=_REQUEST)
        cause = httpx.HTTPStatusError("Client error '401 Unauthorized'", request=_REQUEST, response=response)
        exc = _wrapped(DocumentProcessingError("Failed to process document"), cause)
        assert ue.to_user_reason(exc) == ue._ai_messages("AI model")["auth_error"]

    @pytest.mark.parametrize("cause", [httpx.ReadTimeout("timed out"), httpx.ConnectError("refused")], ids=["timeout", "connect"])
    def test_not_answering(self, cause: BaseException) -> None:
        exc = _wrapped(DocumentProcessingError("Failed to process document"), cause)
        assert ue.to_user_reason(exc) in {
            ue._ai_messages("AI model")["timeout"],
            ue._ai_messages("AI model")["server_error"],
        }
        assert ue.to_user_reason(exc) not in (ue.TEMPORARY_PROBLEM, ue.UNREADABLE_FILE)


class TestBadRequests:
    def test_model_rejects_the_request(self) -> None:
        exc = _wrapped(DocumentProcessingError("Failed to process document"), _status_error(openai.BadRequestError, 400))
        assert ue.to_user_reason(exc) == ue._ai_messages("AI model")["invalid_request"]

    def test_context_length_exceeded_says_the_file_is_too_long_for_the_model(self) -> None:
        response = httpx.Response(400, request=_REQUEST)
        cause = openai.BadRequestError(
            "This model's maximum context length is 8192 tokens", response=response, body=None
        )
        exc = _wrapped(DocumentProcessingError("Failed to process document"), cause)
        assert ue.to_user_reason(exc) == ue._ai_messages("AI model")["request_too_large"]

    def test_embedding_model_rejects_the_request(self) -> None:
        exc = _wrapped(EmbeddingError("Dense embedding failed"), _status_error(openai.BadRequestError, 400))
        assert ue.to_user_reason(exc) == ue._ai_messages("embedding model")["invalid_request"]

    @pytest.mark.parametrize("cause", [ValueError("strange"), KeyError("x")], ids=["value", "key"])
    def test_an_unrecognised_provider_error_is_never_called_a_damaged_file(self, cause: BaseException) -> None:
        class ProviderOddity(Exception):
            pass

        ProviderOddity.__module__ = "litellm.exceptions"
        exc = _wrapped(DocumentProcessingError("Failed to process document"), _wrapped(ProviderOddity("?"), cause))
        assert ue.to_user_reason(exc) == ue._ai_messages("AI model")["invalid_request"]


class _QdrantUnexpected(Exception):
    pass


_QdrantUnexpected.__module__ = "qdrant_client.http.exceptions"


class _GrpcUnavailable(Exception):
    pass


_GrpcUnavailable.__module__ = "grpc.aio._call"


class _LangchainQdrantError(Exception):
    pass


_LangchainQdrantError.__module__ = "langchain_qdrant.vectorstores"


class TestPipesHubServices:
    @pytest.mark.parametrize("cause_type", [_QdrantUnexpected, _GrpcUnavailable], ids=["qdrant", "grpc"])
    def test_search_index_down_while_deleting_old_vectors(self, cause_type: type) -> None:
        # vectorstore.py deletes a record's old vectors before upserting; a
        # down index there is an outage, not the embedding model.
        explicit = _wrapped(VectorStoreError("Failed to delete embeddings"), cause_type("unavailable"))

        def implicit() -> BaseException:
            try:
                try:
                    raise cause_type("unavailable")
                except Exception:
                    raise VectorStoreError("Failed to delete blocks by IDs")  # noqa: B904 - implicit context under test
            except VectorStoreError as raised:
                return raised

        for exc in (explicit, implicit()):
            assert ue.to_user_reason(exc) == ue.TEMPORARY_PROBLEM

    def test_langchain_vector_store_error_is_storage(self) -> None:
        exc = _wrapped(VectorStoreError("Failed to store batch 0"), _LangchainQdrantError("collection not found"))
        assert ue.to_user_reason(exc) == ue.TEMPORARY_PROBLEM
        bare = _LangchainQdrantError("connection refused")
        assert ue.to_user_reason(bare) != ue._ai_messages("AI model")["server_error"]

    def test_storage_timeout_stays_a_temporary_problem(self) -> None:
        exc = _wrapped(VectorStoreError("Failed to store batch 0"), asyncio.TimeoutError())
        assert ue.to_user_reason(exc) == ue.TEMPORARY_PROBLEM

    def test_search_index_over_http_stays_a_temporary_problem(self) -> None:
        exc = _wrapped(VectorStoreError("Failed to store documents in vector store"), httpx.ReadTimeout("timed out"))
        assert ue.to_user_reason(exc) == ue.TEMPORARY_PROBLEM

    def test_own_service_call_while_reading_is_a_temporary_problem(self) -> None:
        from app.services.base_client import ServiceCallError

        cause = ServiceCallError("parsing service unavailable", status_code=503, service_name="parsing")
        exc = _wrapped(DocumentProcessingError("Failed to process document"), cause)
        assert ue.to_user_reason(exc) == ue.TEMPORARY_PROBLEM

    def test_vector_database_down(self) -> None:
        exc = _wrapped(VectorStoreError("Failed to store batch 0"), _QdrantDown("connection refused"))
        assert ue.to_user_reason(exc) == ue.TEMPORARY_PROBLEM

    def test_database_password_error_is_not_a_locked_file(self) -> None:
        exc = _wrapped(DocumentProcessingError("Error updating record status"), _RedisWrongPass("invalid username-password pair"))
        assert ue.to_user_reason(exc) == ue.TEMPORARY_PROBLEM

    def test_connection_error(self) -> None:
        assert ue.to_user_reason(_wrapped(VectorStoreError("x"), ConnectionError())) == ue.TEMPORARY_PROBLEM


class TestHelpers:
    @pytest.mark.parametrize(("ext", "expected"), [("xyz", ".xyz files"), (".KEY", ".key files"), (None, "this type of file"), ("unknown", "this type of file")])
    def test_unsupported_file_type_names_the_extension(self, ext, expected) -> None:
        assert expected in ue.unsupported_file_type(ext)
        assert "PDF, DOCX or TXT" in ue.unsupported_file_type(ext)

    def test_duplicate_failure_carries_the_primary_reason(self) -> None:
        assert ue.duplicate_failed(ue.FILE_TOO_LARGE).endswith(ue.FILE_TOO_LARGE)
        assert ue.duplicate_failed(None).startswith("An identical file failed to index")


_JARGON = re.compile(
    r"Error\b|Exception|vector store|batch|kafka|consumer|graph|blob|record id|traceback|\bid\b|status code|\d{3}",
    re.IGNORECASE,
)


def test_no_reason_shown_to_people_uses_internal_words() -> None:
    texts = [v for k, v in vars(ue).items() if k.isupper() and isinstance(v, str)]
    texts += [*ue._ai_messages("AI model").values(), ue.unsupported_file_type("xyz")]
    for text in texts:
        assert not _JARGON.search(text), text


# Record reasons in the indexing path come from the mapper or a constant, never
# from exception text.
_INDEXING_FILES = [
    "app/services/messaging/kafka/handlers/record.py",
    "app/events/events.py",
    "app/indexing_main.py",
]
_RAW_REASON = re.compile(r"""(reason\s*=|["']reason["']\s*:|\[["']reason["']\]\s*=)\s*(str\(|f["'][^"']*\{|error_msg\b)""")


@pytest.mark.parametrize("path", _INDEXING_FILES)
def test_indexing_code_never_stores_exception_text_as_a_reason(path: str) -> None:
    source = (Path(__file__).resolve().parents[3] / path).read_text(encoding="utf-8")
    assert not _RAW_REASON.findall(source)
