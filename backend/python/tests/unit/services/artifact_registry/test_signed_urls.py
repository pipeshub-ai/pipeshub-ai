"""Tests for `app.services.artifact_registry.signed_urls.SignedUrlBroker` —
download URLs plus the two-phase upload-grant/verified-commit flow."""

from __future__ import annotations

import pytest

from app.services.artifact_registry import signed_urls
from app.services.artifact_registry.signed_urls import (
    GrantExpiredError,
    GrantVerificationError,
    SignedUrlBroker,
)

from .fakes import FakeBlobStore

ORG = "org-1"
USER = "user-1"
MAX_BYTES = 1024


@pytest.fixture(autouse=True)
def _clear_pending_grants():
    """`_PENDING_GRANTS` is a process-local module global (by design — see
    module docstring) so it persists across tests unless reset; without
    this, a grant left un-popped by one test would leak into another's
    `gc_expired_grants()` count."""
    signed_urls._PENDING_GRANTS.clear()
    yield
    signed_urls._PENDING_GRANTS.clear()


class TestGetDownloadUrl:
    async def test_delegates_to_blob_store(self) -> None:
        broker = SignedUrlBroker(FakeBlobStore(), MAX_BYTES)
        url = await broker.get_download_url(org_id=ORG, document_id="doc-1")
        assert url == "https://blob.example/download/doc-1"


class TestGetUploadGrant:
    async def test_issues_grant_within_size_cap(self) -> None:
        broker = SignedUrlBroker(FakeBlobStore(), MAX_BYTES)
        grant = await broker.get_upload_grant(
            org_id=ORG, user_id=USER, artifact_id="art-1", document_id="doc-1",
            declared_size=512, declared_sha256="abc123",
            mime_type="application/pdf",
        )
        assert grant.artifact_id == "art-1"
        assert grant.document_id == "doc-1"
        assert grant.declared_size == 512
        assert grant.upload_url == "https://blob.example/upload/doc-1"

    async def test_rejects_declared_size_over_cap(self) -> None:
        broker = SignedUrlBroker(FakeBlobStore(), MAX_BYTES)
        with pytest.raises(GrantVerificationError):
            await broker.get_upload_grant(
                org_id=ORG, user_id=USER, artifact_id="art-1", document_id="doc-1",
                declared_size=MAX_BYTES + 1, declared_sha256="abc123",
                mime_type="application/pdf",
            )


class TestPopGrant:
    async def test_consumes_grant_exactly_once(self) -> None:
        broker = SignedUrlBroker(FakeBlobStore(), MAX_BYTES)
        grant = await broker.get_upload_grant(
            org_id=ORG, user_id=USER, artifact_id="art-1", document_id="doc-1",
            declared_size=10, declared_sha256="abc123", mime_type="text/plain",
        )
        popped = broker.pop_grant(grant.grant_id, org_id=ORG, user_id=USER)
        assert popped["artifact_id"] == "art-1"

        # Single-use: a second pop for the same grant_id must fail.
        with pytest.raises(GrantExpiredError):
            broker.pop_grant(grant.grant_id, org_id=ORG, user_id=USER)

    async def test_unknown_grant_id_raises_expired(self) -> None:
        broker = SignedUrlBroker(FakeBlobStore(), MAX_BYTES)
        with pytest.raises(GrantExpiredError):
            broker.pop_grant("does-not-exist", org_id=ORG, user_id=USER)

    async def test_grant_for_wrong_actor_raises_expired(self) -> None:
        """Wrong org/user must look identical to "expired" — the pop
        doubles as the ownership check, so it must not distinguish the two
        failure modes to a caller."""
        broker = SignedUrlBroker(FakeBlobStore(), MAX_BYTES)
        grant = await broker.get_upload_grant(
            org_id=ORG, user_id=USER, artifact_id="art-1", document_id="doc-1",
            declared_size=10, declared_sha256="abc123", mime_type="text/plain",
        )
        with pytest.raises(GrantExpiredError):
            broker.pop_grant(grant.grant_id, org_id="other-org", user_id=USER)
        with pytest.raises(GrantExpiredError):
            broker.pop_grant(grant.grant_id, org_id=ORG, user_id="other-user")

    async def test_expired_grant_raises(self, monkeypatch) -> None:
        broker = SignedUrlBroker(FakeBlobStore(), MAX_BYTES)
        timestamps = iter([1_000, 999_999_999])
        monkeypatch.setattr(
            "app.services.artifact_registry.signed_urls.get_epoch_timestamp_in_ms",
            lambda: next(timestamps),
        )
        grant = await broker.get_upload_grant(
            org_id=ORG, user_id=USER, artifact_id="art-1", document_id="doc-1",
            declared_size=10, declared_sha256="abc123", mime_type="text/plain", ttl_s=1,
        )
        with pytest.raises(GrantExpiredError):
            broker.pop_grant(grant.grant_id, org_id=ORG, user_id=USER)


class TestGcExpiredGrants:
    async def test_removes_only_expired_grants(self, monkeypatch) -> None:
        broker = SignedUrlBroker(FakeBlobStore(), MAX_BYTES)
        timestamps = iter([1_000, 5_000_000, 5_000_100, 5_000_200])
        monkeypatch.setattr(
            "app.services.artifact_registry.signed_urls.get_epoch_timestamp_in_ms",
            lambda: next(timestamps),
        )
        expired_grant = await broker.get_upload_grant(
            org_id=ORG, user_id=USER, artifact_id="art-1", document_id="doc-1",
            declared_size=10, declared_sha256="a", mime_type="text/plain", ttl_s=1,
        )
        live_grant = await broker.get_upload_grant(
            org_id=ORG, user_id=USER, artifact_id="art-2", document_id="doc-2",
            declared_size=10, declared_sha256="b", mime_type="text/plain", ttl_s=600,
        )

        removed = SignedUrlBroker.gc_expired_grants()
        assert removed == 1
        with pytest.raises(GrantExpiredError):
            broker.pop_grant(expired_grant.grant_id, org_id=ORG, user_id=USER)
        # Still there.
        broker.pop_grant(live_grant.grant_id, org_id=ORG, user_id=USER)
