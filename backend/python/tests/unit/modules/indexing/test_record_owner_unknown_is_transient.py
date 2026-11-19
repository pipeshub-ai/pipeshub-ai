"""A record whose owner cannot be read now is retried, never failed: it may be mid-create or mid-purge."""
from __future__ import annotations

from app.exceptions.indexing_exceptions import IndexingError
from app.modules.indexing.extension import RecordOwnerUnknown
from app.services.messaging.error_classifier import (
    MessageErrorClassifier,
    MessageErrorType,
)


def test_the_owner_unknown_error_is_an_indexing_error() -> None:
    assert issubclass(RecordOwnerUnknown, IndexingError)


def test_the_classifier_treats_it_as_transient() -> None:
    assert MessageErrorClassifier.classify_by_exception(RecordOwnerUnknown("owner not readable")) == MessageErrorType.TRANSIENT


def test_wrapped_in_an_indexing_error_it_is_still_transient() -> None:
    try:
        try:
            raise RecordOwnerUnknown("owner not readable")
        except RecordOwnerUnknown as exc:
            raise IndexingError(str(exc), details={"record_id": "r1"}) from exc
    except IndexingError as wrapped:
        assert MessageErrorClassifier.classify_by_exception(wrapped) == MessageErrorType.TRANSIENT
