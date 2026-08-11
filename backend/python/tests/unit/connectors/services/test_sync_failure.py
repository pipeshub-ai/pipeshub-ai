"""Unit tests for org-facing sync failure classification."""

import asyncio

from app.connectors.services.sync_failure import SyncFailureCode, classify_sync_failure


class FakeHttpError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


class TestClassifySyncFailure:
    def test_cancelled(self) -> None:
        classified = classify_sync_failure(asyncio.CancelledError())
        assert classified.code == SyncFailureCode.CANCELLED

    def test_auth_invalid_grant(self) -> None:
        classified = classify_sync_failure(RuntimeError("invalid_grant: Token has been expired or revoked."))
        assert classified.code == SyncFailureCode.AUTH
        assert "expired" in classified.reason.lower() or "revoked" in classified.reason.lower()
        assert "invalid_grant:" not in classified.reason

    def test_auth_unauthorized_client_tuple_is_humanized(self) -> None:
        raw = (
            "('unauthorized_client: Client is unauthorized to retrieve access tokens "
            "using this method, or client not authorized for any of the scopes requested.', "
            "{'error': 'unauthorized_client', 'error_description': 'Client is unauthorized "
            "to retrieve access tokens using this method, or client not authorized for any "
            "of the scopes requested.'})"
        )
        classified = classify_sync_failure(RuntimeError(raw))
        assert classified.code == SyncFailureCode.AUTH
        assert "Google blocked" in classified.reason
        assert "{'error'" not in classified.reason
        assert classified.reason.lower().count("unauthorized") <= 1

    def test_permission_403(self) -> None:
        classified = classify_sync_failure(
            FakeHttpError(403, "insufficientFilePermissions for Shared Drive")
        )
        assert classified.code == SyncFailureCode.PERMISSION

    def test_rate_limit(self) -> None:
        classified = classify_sync_failure(FakeHttpError(429, "userRateLimitExceeded"))
        assert classified.code == SyncFailureCode.RATE_LIMIT

    def test_network_timeout(self) -> None:
        classified = classify_sync_failure(TimeoutError("The read operation timed out"))
        assert classified.code == SyncFailureCode.NETWORK

    def test_unknown(self) -> None:
        classified = classify_sync_failure(RuntimeError("something unexpected"))
        assert classified.code == SyncFailureCode.UNKNOWN
        assert classified.reason == "something unexpected"

    def test_reason_is_truncated(self) -> None:
        classified = classify_sync_failure(RuntimeError("x" * 500))
        assert len(classified.reason) <= 180
        assert classified.reason.endswith("…")
