"""Poison-message classification: inputs that fail the same way on every
delivery must be TERMINAL, so a consumer commits them instead of retrying."""
from __future__ import annotations

import json

from app.services.messaging.error_classifier import (
    MessageErrorClassifier,
    MessageErrorType,
)


def _undecodable() -> UnicodeDecodeError:
    try:
        b"\xff\xfe".decode("utf-8")
    except UnicodeDecodeError as e:
        return e
    raise AssertionError("expected a decode error")


class TestUndecodableBytes:
    def test_bytes_that_are_not_utf8_are_terminal(self) -> None:
        assert MessageErrorClassifier.classify_by_exception(_undecodable()) == MessageErrorType.TERMINAL

    def test_a_decode_error_wrapped_by_a_handler_is_still_terminal(self) -> None:
        try:
            try:
                raise _undecodable()
            except UnicodeDecodeError as inner:
                raise RuntimeError("could not read message body") from inner
        except RuntimeError as wrapped:
            assert MessageErrorClassifier.classify_by_exception(wrapped) == MessageErrorType.TERMINAL

    def test_a_network_error_raised_while_handling_a_decode_error_stays_retryable(self) -> None:
        # An encoding fallback that hits the network: the decode error is only
        # incidental context, not the reason this attempt failed.
        try:
            try:
                raise _undecodable()
            except UnicodeDecodeError:
                raise ConnectionError("storage unreachable while fetching the fallback copy")  # noqa: B904
        except ConnectionError as network_error:
            assert network_error.__cause__ is None
            assert isinstance(network_error.__context__, UnicodeDecodeError)
            assert MessageErrorClassifier.classify_by_exception(network_error) == MessageErrorType.TRANSIENT

    def test_malformed_json_stays_terminal(self) -> None:
        try:
            json.loads("{not json")
        except json.JSONDecodeError as e:
            assert MessageErrorClassifier.classify_by_exception(e) == MessageErrorType.TERMINAL


class TestStorageRejections:
    """A record the storage service refuses is refused again on every retry."""

    def test_a_storage_rejection_is_terminal(self) -> None:
        import aiohttp

        from app.modules.transformers.blob_storage import _storage_status_error

        for status in (400, 413):
            error = _storage_status_error(status, "Failed to upload record", "larger than the 30 MB limit")
            assert MessageErrorClassifier.classify_by_exception(error) == MessageErrorType.TERMINAL
            assert isinstance(error, aiohttp.ClientError), "callers catching ClientError still catch it"

    def test_a_storage_rejection_wrapped_by_a_handler_is_still_terminal(self) -> None:
        from app.modules.transformers.blob_storage import _storage_status_error

        try:
            try:
                raise _storage_status_error(400, "Failed to upload record")
            except Exception as inner:
                raise RuntimeError("event processor failed") from inner
        except RuntimeError as outer:
            assert MessageErrorClassifier.classify_by_exception(outer) == MessageErrorType.TERMINAL

    def test_a_refused_service_token_is_not_treated_as_a_rejection(self) -> None:
        from app.modules.transformers.blob_storage import _storage_status_error

        error = _storage_status_error(401, "Failed to upload record")
        assert MessageErrorClassifier.classify_by_exception(error) == MessageErrorType.TRANSIENT
