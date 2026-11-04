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

from app.config.constants.arangodb import Connectors, OriginTypes, RecordTypes
from app.exceptions.indexing_exceptions import (
    BlockContainerValidationError,
    DocumentProcessingError,
    EmbeddingError,
    EmbeddingModelUnavailableError,
    EmbeddingNotConfiguredError,
    IndexingError,
    VectorStoreError,
)
from app.exceptions.storage_exceptions import StorageRequestRejectedError
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

    def test_ai_settings_that_could_not_be_read_are_a_temporary_problem(self) -> None:
        from app.exceptions.indexing_exceptions import AISettingsUnavailableError

        unread = AISettingsUnavailableError("Could not read the workspace's AI settings")
        unread.__cause__ = KeyError("etcd key")  # a cause no other rule recognises
        assert ue.to_user_reason(unread) == ue.TEMPORARY_PROBLEM

    def test_no_model_that_reads_images_gives_the_scanned_document_reason_even_when_wrapped(self) -> None:
        from app.exceptions.indexing_exceptions import MultimodalModelNotConfiguredError

        missing = MultimodalModelNotConfiguredError("No multimodal LLM found in configuration.")
        assert ue.to_user_reason(missing) == ue.SCANNED_DOCUMENT_NEEDS_OCR
        # The in-process OCR path wraps whatever OCR raised.
        wrapped = _wrapped(DocumentProcessingError(f"Failed to process document: {missing}"), missing)
        assert ue.to_user_reason(wrapped) == ue.SCANNED_DOCUMENT_NEEDS_OCR

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

    def test_the_typed_password_error_gives_the_password_reason_without_any_wording(self) -> None:
        from app.exceptions.indexing_exceptions import PasswordProtectedError

        exc = _wrapped(DocumentProcessingError("Failed to process document"), PasswordProtectedError("x"))
        assert ue.to_user_reason(exc) == ue.PASSWORD_PROTECTED
        assert ue.is_password_protected(exc)

    def test_status_follows_the_type_not_the_text(self) -> None:
        # Both read as password-protected to a person; only the typed one is a verdict on the file.
        worded = _wrapped(DocumentProcessingError("Failed to process document"), ValueError("File has not been decrypted"))
        assert ue.to_user_reason(worded) == ue.PASSWORD_PROTECTED
        assert not ue.is_password_protected(worded)
        assert not ue.is_password_protected(DocumentProcessingError("the password column is missing"))
        assert not ue.is_password_protected(None)

    def test_the_parsing_service_code_counts_as_a_verdict(self) -> None:
        from app.services.parsing.client import ParsingClientError
        from app.services.parsing.interface import ParseErrorCode

        assert ue.is_password_protected(ParsingClientError(ParseErrorCode.PASSWORD_REQUIRED, "locked"))
        assert not ue.is_password_protected(ParsingClientError(ParseErrorCode.PARSE_FAILED, "locked"))

    def test_a_copy_of_a_locked_file_is_not_a_classification_failure(self) -> None:
        from app.services.graph_db.interface.graph_db_provider import (
            promoted_duplicate_extraction_status,
        )

        assert promoted_duplicate_extraction_status("PASSWORD_PROTECTED", {}) == "SKIPPED"
        assert promoted_duplicate_extraction_status("FAILED", {}) == "FAILED"

    def test_a_copy_of_a_locked_file_says_so(self) -> None:
        assert "password-protected" in ue.duplicate_password_protected(ue.PASSWORD_PROTECTED)
        assert "failed" not in ue.duplicate_password_protected(None)

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


_UPLOAD_ONLY = (ue.UNREADABLE_FILE, ue.FILE_TOO_LARGE, ue.PASSWORD_PROTECTED, ue.PROCESSING_TIMED_OUT)


def _synced(record_type: str, connector: str | None = None) -> dict:
    record = {"origin": OriginTypes.CONNECTOR.value, "recordType": record_type}
    if connector is not None:
        record["connectorName"] = connector
    return record


class TestSyncedItems:
    """A connector's items were never uploaded, so their reasons can't say to upload them again."""

    def test_a_pull_request_with_content_that_cannot_be_processed(self) -> None:
        reason = ue.to_user_reason(
            BlockContainerValidationError([], record_id="r1"),
            record=_synced(RecordTypes.PULL_REQUEST.value), source="GitHub Apache",
        )
        assert reason.startswith("We couldn't read this pull request from GitHub Apache:")
        assert "upload" not in reason.lower()

    def test_a_pull_request_the_storage_service_refuses_for_its_size(self) -> None:
        refused = StorageRequestRejectedError("One of the files is larger than the 100 MB limit.", 400, too_large=True)
        reason = ue.to_user_reason(refused, record=_synced(RecordTypes.PULL_REQUEST.value), source="GitHub Apache")
        assert reason.startswith("This pull request from GitHub Apache is larger than PipesHub can index")
        assert "upload" not in reason.lower()

    def test_a_synced_file_is_fixed_in_its_source(self) -> None:
        reason = ue.to_user_reason(
            _wrapped(DocumentProcessingError("x"), MemoryError()),
            record=_synced(RecordTypes.FILE.value), source="Google Drive",
        )
        assert reason.startswith("This file from Google Drive is too large to index,")
        assert "added to Google Drive are indexed on the next sync" in reason

    def test_a_github_pull_request_is_not_told_to_reindex_it(self) -> None:
        # The app refuses Reindex on one record of a code repository.
        reason = ue.to_user_reason(
            BlockContainerValidationError([], record_id="r1"),
            record=_synced(RecordTypes.PULL_REQUEST.value, Connectors.GITHUB_TEAMS.value),
            source="GitHub Apache",
        )
        assert "Reindex" not in reason
        assert "indexed again on the next sync after it changes in GitHub Apache" in reason

    def test_a_slow_code_file_points_at_a_repository_reindex(self) -> None:
        reason = ue.for_synced_item(
            ue.PROCESSING_TIMED_OUT, RecordTypes.CODE_FILE.value, "GitLab", Connectors.GITLAB.value,
        )
        assert "Reindex the whole repository from the knowledge hub" in reason
        assert "Try Reindex" not in reason

    @pytest.mark.parametrize("connector", sorted(ue._CODE_REPO_CONNECTORS))
    @pytest.mark.parametrize("record_type", [*sorted(ue._SYNCED_FILE_TYPES), *sorted(ue._SYNCED_ITEM_KINDS), None])
    @pytest.mark.parametrize("reason", _UPLOAD_ONLY)
    def test_no_code_repository_item_is_told_to_reindex_it_alone(
        self, reason: str, record_type: str | None, connector: str,
    ) -> None:
        assert "Try Reindex" not in ue.for_synced_item(reason, record_type, "Repo", connector.lower())

    def test_other_connectors_can_still_reindex_one_item(self) -> None:
        reason = ue.for_synced_item(ue.UNREADABLE_FILE, RecordTypes.TICKET.value, "Jira", Connectors.JIRA.value)
        assert "Try Reindex after it changes in Jira" in reason

    @pytest.mark.parametrize("record_type", [*sorted(ue._SYNCED_FILE_TYPES), *sorted(ue._SYNCED_ITEM_KINDS)])
    def test_a_password_protected_item_is_not_assumed_editable_in_its_source(self, record_type: str) -> None:
        # A mail attachment can't have its password removed where it lives.
        reason = ue.for_synced_item(ue.PASSWORD_PROTECTED, record_type, "Gmail")
        assert "Remove the password" not in reason

    @pytest.mark.parametrize("record_type", [*sorted(ue._SYNCED_FILE_TYPES), *sorted(ue._SYNCED_ITEM_KINDS), "SOMETHING_NEW", None])
    @pytest.mark.parametrize("reason", _UPLOAD_ONLY)
    def test_no_synced_item_is_told_to_upload(self, reason: str, record_type: str | None) -> None:
        synced = ue.for_synced_item(reason, record_type, "Jira")
        assert synced != reason
        assert "upload" not in synced.lower()
        assert "from Jira" in synced
        assert not _JARGON.search(synced), synced

    def test_an_unknown_source_still_reads_as_a_sentence(self) -> None:
        assert ue.for_synced_item(ue.UNREADABLE_FILE, RecordTypes.TICKET.value, None).startswith(
            "We couldn't read this ticket from the source:"
        )

    def test_other_reasons_are_unchanged(self) -> None:
        for reason in (ue.TEMPORARY_PROBLEM, ue.GENERIC_FAILURE, ue.EMBEDDING_NOT_CONFIGURED):
            assert ue.for_synced_item(reason, RecordTypes.PULL_REQUEST.value, "GitHub") == reason

    @pytest.mark.parametrize("record", [None, {"origin": OriginTypes.UPLOAD.value, "recordType": RecordTypes.FILE.value}, {}])
    def test_uploads_keep_todays_wording(self, record: dict | None) -> None:
        assert ue.to_user_reason(_wrapped(DocumentProcessingError("x"), MemoryError()), record=record) == ue.FILE_TOO_LARGE


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



class TestProviderErrorCode:
    def test_a_google_genai_key_rejection_is_an_auth_error(self) -> None:
        from google.genai import errors as genai_errors

        body = {"error": {"code": 401, "message": "API key not valid", "status": "UNAUTHENTICATED"}}
        assert ue.provider_error_code(genai_errors.ClientError(401, body)) == "auth_error"

    def test_a_missing_model_is_model_not_found(self) -> None:
        request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
        error = openai.NotFoundError("not found", response=httpx.Response(404, request=request), body=None)
        assert ue.provider_error_code(error) == "model_not_found"

    @pytest.mark.parametrize(
        ("status", "error_code", "expected"),
        (
            (400, "ValidationException", "invalid_request"),
            (403, "AccessDeniedException", "auth_error"),
            (429, "ThrottlingException", "rate_limit"),
        ),
    )
    def test_a_bedrock_client_error_is_read_by_its_http_status(
        self, status: int, error_code: str, expected: str,
    ) -> None:
        from botocore.exceptions import ClientError

        response = {
            "Error": {"Code": error_code, "Message": "rejected"},
            "ResponseMetadata": {"HTTPStatusCode": status},
        }
        assert ue.provider_error_code(ClientError(response, "Converse")) == expected


def _bedrock_error(
    code: str, message: str, operation: str = "Converse", status: int = 403,
) -> BaseException:
    from botocore.exceptions import ClientError

    return ClientError(
        {"Error": {"Code": code, "Message": message}, "ResponseMetadata": {"HTTPStatusCode": status}},
        operation,
    )


def _stored(cause: BaseException) -> BaseException:
    """The chain a non-retriable embedding failure reaches `to_user_reason` as.

    BedrockEmbeddings re-raises botocore's ClientError, `_embed_documents_with_retry`
    lets a non-retriable one out unwrapped, and the vector store wraps it twice.
    """
    return _wrapped(
        VectorStoreError("Failed to store documents in vector store"),
        _wrapped(VectorStoreError("Failed to store batch 0"), cause),
    )


class TestBedrockAccessDeniedCause:
    @pytest.mark.parametrize(
        ("code", "message"),
        (
            ("UnrecognizedClientException", "The security token included in the request is invalid."),
            ("InvalidSignatureException", "The request signature we calculated does not match the signature you provided."),
            ("ExpiredTokenException", "The security token included in the request is expired"),
            ("InvalidClientTokenId", "The security token included in the request is invalid."),
        ),
    )
    def test_a_wrong_or_expired_key_is_its_own_cause(self, code: str, message: str) -> None:
        assert ue.bedrock_access_denied_cause(_bedrock_error(code, message)) == "credentials"

    def test_the_cause_is_read_through_a_wrapper(self) -> None:
        exc = _wrapped(
            RuntimeError("Converse call failed"),
            _bedrock_error("UnrecognizedClientException", "The security token included in the request is invalid."),
        )
        assert ue.bedrock_access_denied_cause(exc) == "credentials"

    def test_an_iam_denial_is_not_a_credential_failure(self) -> None:
        exc = _bedrock_error(
            "AccessDeniedException",
            "User: arn:aws:iam::1:user/x is not authorized to perform: bedrock:InvokeModel",
        )
        assert ue.bedrock_access_denied_cause(exc) == "iam"

    def test_no_exception_has_no_cause(self) -> None:
        assert ue.bedrock_access_denied_cause(None) is None


class TestBedrockDenialReason:
    @pytest.mark.parametrize(
        "message",
        (
            "openai.gpt-6-luna is not available for this account.",
            "explicit deny in a service control policy",
            "Something new went wrong",
            "Model access is denied due to INVALID_PAYMENT_INSTRUMENT. Your AWS Marketplace "
            "subscription for this model cannot be completed.",
        ),
    )
    def test_an_embedding_denial_that_is_not_a_bad_key_does_not_blame_the_key(self, message: str) -> None:
        reason = ue.to_user_reason(_stored(_bedrock_error("AccessDeniedException", message, "InvokeModel")))
        assert "API key" not in reason
        assert "embedding model's provider refused access to the model" in reason

    def test_an_embedding_bad_key_points_to_the_key(self) -> None:
        error = _bedrock_error(
            "UnrecognizedClientException", "The security token included in the request is invalid.", "InvokeModel",
        )
        assert ue.to_user_reason(_stored(error)) == ue._ai_messages("embedding model")["auth_error"]

    def test_an_embedding_iam_denial_asks_for_access_not_a_new_key(self) -> None:
        error = _bedrock_error(
            "AccessDeniedException",
            "User: arn:aws:iam::1:user/x is not authorized to perform: bedrock:InvokeModel",
            "InvokeModel",
        )
        reason = ue.to_user_reason(_stored(error))
        assert "API key" not in reason
        assert "aren't allowed to use the model" in reason
        assert "bedrock:InvokeModel" in reason

    def test_an_embedding_denial_under_an_embedding_error_reads_the_same(self) -> None:
        error = _bedrock_error("AccessDeniedException", "is not authorized to perform: bedrock:InvokeModel", "InvokeModel")
        exc = _wrapped(EmbeddingError("Dense embedding failed"), error)
        assert ue.to_user_reason(exc) == ue.to_user_reason(_stored(error))

    @pytest.mark.parametrize("status", (400, 401))
    def test_an_embedding_not_authorized_error_asks_for_access(self, status: int) -> None:
        error = _bedrock_error(
            "NotAuthorized", "You do not have permission to perform this action.", "InvokeModel", status=status,
        )
        assert ue.provider_error_code(error) == "auth_error"
        assert ue.bedrock_access_denied_cause(error) == "iam"
        assert ue.to_user_reason(_stored(error)) == ue._ai_messages("embedding model")["permission_denied"]

    def test_bedrock_throttling_while_storing_is_the_model_being_busy(self) -> None:
        error = _bedrock_error("ThrottlingException", "Too many requests", "InvokeModel", status=429)
        assert ue.to_user_reason(_stored(error)) == ue._ai_messages("embedding model")["rate_limit"]

    def test_a_chat_model_iam_denial_while_processing_asks_for_access(self) -> None:
        error = _bedrock_error("AccessDeniedException", "is not authorized to perform: bedrock:InvokeModel")
        exc = _wrapped(DocumentProcessingError("Failed to process document"), error)
        assert "The AI model's credentials work but aren't allowed" in ue.to_user_reason(exc)

    def test_a_connector_s3_denial_while_storing_stays_a_store_failure(self) -> None:
        error = _bedrock_error("AccessDenied", "Access Denied", "GetObject")
        assert ue.to_user_reason(_stored(error)) == ue.TEMPORARY_PROBLEM

    def test_a_forbidden_response_from_another_provider_still_points_to_the_key(self) -> None:
        exc = _wrapped(EmbeddingError("Dense embedding failed"), _status_error(openai.PermissionDeniedError, 403))
        assert "rejected PipesHub's API key" in ue.to_user_reason(exc)


class TestRepositoryFileReasons:
    @pytest.mark.parametrize(
        ("size_bytes", "limit_bytes", "size_text", "limit_text"),
        [
            (5 * 1024 * 1024 + 1, 5 * 1024 * 1024, "5.1 MB", "up to 5 MB"),
            (52_428_800, 5 * 1024 * 1024, "50 MB", "up to 5 MB"),
            (13_107_200, 20 * 1024 * 1024, "12.5 MB", "up to 20 MB"),
        ],
    )
    def test_too_large_names_both_sizes_and_the_setting(
        self, size_bytes: int, limit_bytes: int, size_text: str, limit_text: str
    ) -> None:
        reason = ue.text_file_too_large(size_bytes, limit_bytes, repository_file=True)

        assert f"This file is {size_text}" in reason
        assert limit_text in reason
        assert "CODE_FILE_MAX_SIZE_MB" in reason

    def test_too_large_ends_with_a_step_the_reader_can_take(self) -> None:
        """A record marked File Type Not Supported has no Reindex action, so
        "reindex it" would send people looking for a button that is not there."""
        five_mb = 5 * 1024 * 1024

        from_repository = ue.text_file_too_large(five_mb + 1, five_mb, repository_file=True)
        uploaded = ue.text_file_too_large(five_mb + 1, five_mb, repository_file=False)

        assert from_repository.endswith("and then choose Index all on the repository.")
        assert uploaded.endswith("and then upload it again.")
        assert "Reindex it" not in from_repository + uploaded

    @pytest.mark.parametrize("message", [ue.PARSE_WORKER_OUT_OF_MEMORY, ue.PROCESSING_TIMED_OUT])
    def test_a_parse_worker_failure_keeps_the_reason_it_was_raised_with(self, message: str) -> None:
        try:
            raise DocumentProcessingError("Failed to process document") from RuntimeError(message)
        except DocumentProcessingError as wrapped:
            assert ue.to_user_reason(wrapped) == message


class TestRateLimitedSource:
    # 2026-01-15 10:00:00 UTC
    NOW = 1_768_471_200_000

    def test_a_wait_names_the_source_and_the_time_it_ends(self) -> None:
        reason = ue.rate_limited_will_retry("Engineering GitHub", self.NOW + 35 * 60_000, self.NOW)
        assert reason == (
            "Engineering GitHub is limiting how often PipesHub can read from it, so indexing "
            "this file is paused. It will retry automatically after 10:35 UTC."
        )

    def test_a_wait_into_another_day_says_which(self) -> None:
        reason = ue.rate_limited_will_retry(None, self.NOW + 15 * 3600 * 1000, self.NOW)
        assert reason.startswith("The source is limiting")
        assert reason.endswith("after 01:00 UTC on 16 Jan.")

    def test_giving_up_says_why_and_when_to_try_again(self) -> None:
        reason = ue.rate_limited_gave_up("Engineering GitHub", self.NOW + 30 * 60_000, self.NOW, 24)
        assert "kept limiting" in reason
        assert "more than 24 hours" in reason
        assert reason.endswith("Reindex it after 10:30 UTC.")



class TestStorageRefusal:
    """A record the storage service refuses is reported for what it is, not as a temporary problem."""

    def test_a_record_refused_as_too_large_says_the_file_is_too_large(self) -> None:
        from app.exceptions.storage_exceptions import StorageRequestRejectedError
        from app.utils.user_errors import FILE_TOO_LARGE, to_user_reason

        error = StorageRequestRejectedError("refused (HTTP 400): larger than the 30 MB limit", 400, too_large=True)
        assert to_user_reason(error) == FILE_TOO_LARGE

    def test_another_refusal_is_a_plain_failure_not_a_temporary_one(self) -> None:
        from app.exceptions.storage_exceptions import StorageRequestRejectedError
        from app.utils.user_errors import GENERIC_FAILURE, to_user_reason

        assert to_user_reason(StorageRequestRejectedError("refused (HTTP 415)", 415)) == GENERIC_FAILURE


class TestTooLargeRefusal:
    """Only the storage service refusing the content for its size, wherever it sits in the chain."""

    @staticmethod
    def _refusal(too_large: bool = True) -> Exception:
        from app.exceptions.storage_exceptions import StorageRequestRejectedError

        return StorageRequestRejectedError("refused (HTTP 400): larger than the 30 MB limit", 400, too_large=too_large)

    def test_the_refusal_itself(self) -> None:
        refusal = self._refusal()
        assert ue.too_large_refusal(refusal) is refusal

    def test_as_the_cause_of_a_wrapper(self) -> None:
        try:
            raise RuntimeError("Failed to save record") from self._refusal()
        except RuntimeError as wrapped:
            assert ue.too_large_refusal(wrapped) is wrapped.__cause__

    def test_as_the_context_of_a_later_error(self) -> None:
        try:
            try:
                raise self._refusal()
            except Exception:
                raise ValueError("cleanup failed")  # noqa: B904 - the implicit context is the point
        except ValueError as later:
            assert ue.too_large_refusal(later) is later.__context__

    def test_a_refusal_for_another_reason(self) -> None:
        assert ue.too_large_refusal(self._refusal(too_large=False)) is None

    def test_other_too_large_reasons_do_not_count(self) -> None:
        assert ue.to_user_reason(MemoryError()) == ue.FILE_TOO_LARGE
        assert ue.too_large_refusal(MemoryError()) is None
        assert ue.too_large_refusal(ValueError("file is too large")) is None

    def test_none(self) -> None:
        assert ue.too_large_refusal(None) is None

    def test_a_cyclic_chain_terminates(self) -> None:
        a, b = ValueError("a"), ValueError("b")
        a.__cause__, b.__cause__ = b, a
        assert ue.too_large_refusal(a) is None
