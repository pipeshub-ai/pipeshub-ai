class IndexingError(Exception):
    """Base exception for indexing-related errors"""

    def __init__(self, message: str, record_id: str = None, details: dict = None) -> None:
        self.message = message
        self.record_id = record_id
        self.details = details or {}
        super().__init__(self.message)


class DocumentProcessingError(IndexingError):
    """Raised when there's an error processing a document"""

    def __init__(
        self,
        message: str = "Failed to process document",
        doc_id: str = None,
        details: dict = None,
    ) -> None:
        super().__init__(message, doc_id, details)
        self.doc_id = doc_id


class PasswordProtectedError(DocumentProcessingError):
    """The file is encrypted and needs a password to open.

    Raised only on positive evidence from the file itself (a PDF library's
    password error, an Office encryption container), never from the wording of
    another error. Terminal like every ``DocumentProcessingError``: a retry
    reads the same bytes, so the record ends ``PASSWORD_PROTECTED`` instead of
    ``FAILED``.
    """

    def __init__(
        self,
        message: str = "The file is password-protected",
        doc_id: str = None,
        details: dict = None,
    ) -> None:
        super().__init__(message, doc_id, details)


class MultimodalModelNotConfiguredError(DocumentProcessingError):
    """No AI model that can read images is set up, so OCR cannot read the file.

    A fact about the workspace's AI settings, not an outage: a retry fails the
    same way until an admin adds such a model, so it is terminal like every
    ``DocumentProcessingError``. Failing to *read* the settings is not this
    error; that is ``AISettingsUnavailableError``.
    """


class AISettingsUnavailableError(IndexingError):
    """The workspace's AI settings could not be read (the store did not answer).

    An outage, not a fact about the file, so it is retried: deliberately not a
    ``DocumentProcessingError``, which the message classifier treats as final.
    Raised from the store's own error, which stays the cause.
    """


class RecordDeletedError(IndexingError):
    """The record was deleted (or moved to the trash) while it was being indexed.

    An expected outcome, not a fault: there is no record left to write a status
    to. Terminal, since a retry finds the record gone too. Deliberately
    not a ``DocumentProcessingError``: that writes FAILED onto the record and
    sends its queued MD5 duplicates to FAILED with it. Raise it only from a
    read that propagates graph errors (``raise_on_error=True``), so an outage
    is never mistaken for a deletion.
    """

    def __init__(self, record_id: str) -> None:
        super().__init__(f"record {record_id} was deleted while it was being indexed", record_id)


class RecordStatusUpdateError(IndexingError):
    """Raised when persisting a record's status fields to the graph DB fails.

    Deliberately not a DocumentProcessingError: a failed DB write is an
    infra blip (retry may succeed), not a content-processing failure (which
    will fail the same way every time) — see
    MessageErrorClassifier.classify_by_exception, which treats IndexingError
    subtypes other than the terminal ones (DocumentProcessingError,
    ChunkingError, ExtractionError, ProcessingError,
    BlockContainerValidationError) as transient/retryable.
    """

    pass


class EmbeddingError(IndexingError):
    """Raised when there's an error creating embeddings"""

    pass


class EmbeddingModelUnavailableError(IndexingError):
    """The embedding model could not be built or did not answer a probe."""

    pass


class EmbeddingNotConfiguredError(EmbeddingModelUnavailableError):
    """No embedding model is configured and the local fallback is unavailable."""

    pass


class VectorStoreError(IndexingError):
    """Raised when there's an error interacting with the vector store"""

    pass


class EntityLookupUnavailableError(IndexingError):
    """An entity-resolution lookup (vector or graph) could not run.

    Never the same as "no match": answering it with a new node is how
    duplicates are minted. Always retryable, whatever the underlying cause,
    since the record's content is fine; the record's resolution runs again on
    redelivery.
    """

    pass


class MetadataProcessingError(IndexingError):
    """Raised when there's an error processing document metadata"""

    pass


class ChunkingError(IndexingError):
    """Raised when there's an error during document chunking"""

    pass


class EmbeddingDeletionError(IndexingError):
    """Raised when there's an error deleting embeddings"""

    def __init__(
        self,
        message: str = "Failed to delete embeddings",
        record_id: str = None,
        details: dict = None,
    ) -> None:
        super().__init__(message, record_id, details)
        self.record_id = record_id


class ExtractionError(IndexingError):
    """Raised when there's an error extracting content"""

    pass


class ProcessingError(IndexingError):
    """Raised when there's an error processing the document"""

    pass


class BlockContainerValidationError(IndexingError):
    """Raised when block container validation fails before indexing.

    ``errors`` is a list of :class:`ValidationIssue` objects (imported lazily
    to avoid circular imports with the validator module).
    """

    def __init__(
        self,
        errors: list,
        record_id: str = None,
        virtual_record_id: str = None,
        record_name: str = None,
    ) -> None:
        self.errors = errors
        self.record_id = record_id
        self.virtual_record_id = virtual_record_id
        self.record_name = record_name

        # Build record context prefix
        context_parts = []
        if record_id:
            context_parts.append(f"record_id={record_id}")
        if virtual_record_id:
            context_parts.append(f"vrid={virtual_record_id}")
        if record_name:
            context_parts.append(f"name={record_name!r}")
        context = f"[{', '.join(context_parts)}] " if context_parts else ""

        msg = "; ".join(
            f"[{e.location}] {e.code}: {e.message}" for e in errors
        )
        super().__init__(
            f"{context}Block container validation failed — {msg}",
            record_id=record_id,
        )
