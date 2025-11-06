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


class EmbeddingError(IndexingError):
    """Raised when there's an error creating embeddings"""



class VectorStoreError(IndexingError):
    """Raised when there's an error interacting with the vector store"""



class MetadataProcessingError(IndexingError):
    """Raised when there's an error processing document metadata"""



class ChunkingError(IndexingError):
    """Raised when there's an error during document chunking"""



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



class ProcessingError(IndexingError):
    """Raised when there's an error processing the document"""

