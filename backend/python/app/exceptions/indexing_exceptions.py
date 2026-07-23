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


class VectorStoreError(IndexingError):
    """Raised when there's an error interacting with the vector store"""

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
