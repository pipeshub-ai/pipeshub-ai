"""Tests for app.exceptions.indexing_exceptions"""

import pytest

from app.exceptions.indexing_exceptions import (
    ChunkingError,
    DocumentProcessingError,
    EmbeddingDeletionError,
    EmbeddingError,
    ExtractionError,
    IndexingError,
    MetadataProcessingError,
    ProcessingError,
    VectorStoreError,
)


# ---------------------------------------------------------------------------
# IndexingError (base)
# ---------------------------------------------------------------------------


class TestIndexingError:
    """Tests for the base IndexingError exception."""

    def test_message_attribute(self):
        err = IndexingError("Something went wrong")
        assert err.message == "Something went wrong"

    def test_record_id_attribute(self):
        err = IndexingError("fail", record_id="rec-123")
        assert err.record_id == "rec-123"

    def test_details_attribute(self):
        details = {"step": "embedding", "count": 5}
        err = IndexingError("fail", details=details)
        assert err.details == details

    def test_details_defaults_to_empty_dict(self):
        err = IndexingError("fail")
        assert err.details == {}

    def test_record_id_defaults_to_none(self):
        err = IndexingError("fail")
        assert err.record_id is None

    def test_str_representation(self):
        err = IndexingError("Something went wrong")
        assert str(err) == "Something went wrong"

    def test_is_exception(self):
        err = IndexingError("fail")
        assert isinstance(err, Exception)

    def test_can_be_raised_and_caught(self):
        with pytest.raises(IndexingError) as exc_info:
            raise IndexingError("boom", record_id="r1", details={"k": "v"})
        assert exc_info.value.message == "boom"
        assert exc_info.value.record_id == "r1"
        assert exc_info.value.details == {"k": "v"}


# ---------------------------------------------------------------------------
# Inheritance hierarchy
# ---------------------------------------------------------------------------


class TestInheritanceHierarchy:
    """All subclasses should inherit from IndexingError."""

    @pytest.mark.parametrize(
        "exc_cls",
        [
            DocumentProcessingError,
            EmbeddingError,
            VectorStoreError,
            MetadataProcessingError,
            ChunkingError,
            EmbeddingDeletionError,
            ExtractionError,
            ProcessingError,
        ],
    )
    def test_subclass_of_indexing_error(self, exc_cls):
        assert issubclass(exc_cls, IndexingError)

    @pytest.mark.parametrize(
        "exc_cls",
        [
            DocumentProcessingError,
            EmbeddingError,
            VectorStoreError,
            MetadataProcessingError,
            ChunkingError,
            EmbeddingDeletionError,
            ExtractionError,
            ProcessingError,
        ],
    )
    def test_subclass_of_exception(self, exc_cls):
        assert issubclass(exc_cls, Exception)

    @pytest.mark.parametrize(
        "exc_cls",
        [
            EmbeddingError,
            VectorStoreError,
            MetadataProcessingError,
            ChunkingError,
            ExtractionError,
            ProcessingError,
        ],
    )
    def test_simple_subclass_inherits_constructor(self, exc_cls):
        """Subclasses without custom __init__ accept the base constructor args."""
        err = exc_cls("msg", record_id="r1", details={"x": 1})
        assert err.message == "msg"
        assert err.record_id == "r1"
        assert err.details == {"x": 1}

    @pytest.mark.parametrize(
        "exc_cls",
        [
            EmbeddingError,
            VectorStoreError,
            MetadataProcessingError,
            ChunkingError,
            ExtractionError,
            ProcessingError,
        ],
    )
    def test_simple_subclass_can_be_caught_as_indexing_error(self, exc_cls):
        with pytest.raises(IndexingError):
            raise exc_cls("caught as base")


# ---------------------------------------------------------------------------
# DocumentProcessingError
# ---------------------------------------------------------------------------


class TestDocumentProcessingError:
    """Tests for DocumentProcessingError with its custom doc_id attribute."""

    def test_default_message(self):
        err = DocumentProcessingError()
        assert err.message == "Failed to process document"

    def test_custom_message(self):
        err = DocumentProcessingError("custom msg")
        assert err.message == "custom msg"

    def test_doc_id_attribute(self):
        err = DocumentProcessingError(doc_id="doc-456")
        assert err.doc_id == "doc-456"
        # doc_id is also passed as record_id to the base class
        assert err.record_id == "doc-456"

    def test_details(self):
        err = DocumentProcessingError(details={"page": 3})
        assert err.details == {"page": 3}

    def test_str_representation(self):
        err = DocumentProcessingError("parse error")
        assert str(err) == "parse error"


# ---------------------------------------------------------------------------
# EmbeddingDeletionError
# ---------------------------------------------------------------------------


class TestEmbeddingDeletionError:
    """Tests for EmbeddingDeletionError with its custom default message."""

    def test_default_message(self):
        err = EmbeddingDeletionError()
        assert err.message == "Failed to delete embeddings"

    def test_custom_message(self):
        err = EmbeddingDeletionError("custom delete error")
        assert err.message == "custom delete error"

    def test_record_id_attribute(self):
        err = EmbeddingDeletionError(record_id="emb-789")
        assert err.record_id == "emb-789"

    def test_details(self):
        err = EmbeddingDeletionError(details={"vector_ids": [1, 2, 3]})
        assert err.details == {"vector_ids": [1, 2, 3]}

    def test_str_representation(self):
        err = EmbeddingDeletionError()
        assert str(err) == "Failed to delete embeddings"


# ---------------------------------------------------------------------------
# String representation across all subclasses
# ---------------------------------------------------------------------------


class TestStringRepresentation:
    """Verify str() returns the message for every exception class."""

    @pytest.mark.parametrize(
        "exc_cls,args,expected",
        [
            (IndexingError, ("base error",), "base error"),
            (DocumentProcessingError, ("doc error",), "doc error"),
            (EmbeddingError, ("emb error",), "emb error"),
            (VectorStoreError, ("vec error",), "vec error"),
            (MetadataProcessingError, ("meta error",), "meta error"),
            (ChunkingError, ("chunk error",), "chunk error"),
            (EmbeddingDeletionError, ("del error",), "del error"),
            (ExtractionError, ("ext error",), "ext error"),
            (ProcessingError, ("proc error",), "proc error"),
        ],
    )
    def test_str_returns_message(self, exc_cls, args, expected):
        err = exc_cls(*args)
        assert str(err) == expected
