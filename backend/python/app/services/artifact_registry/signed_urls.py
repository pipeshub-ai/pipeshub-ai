"""`SignedUrlBroker` — user-facing download URLs, and the two-phase
upload-grant + verified-commit flow for content too large to pass inline
through a tool call.

Security posture (see plan section 9):
  * Every method takes an already-authorized `record` (callers must have
    gone through `AccessPolicy` first — this module never checks
    permissions itself, staying single-responsibility).
  * Grants are short-lived, single-use (removed from `_PENDING_GRANTS` on
    commit or expiry), and bound to a server-derived object key — the
    caller never supplies a path.
  * `commit_version` re-downloads and re-hashes the actual stored bytes
    rather than trusting the client's PUT to have matched what it
    declared — a compromised/buggy uploader cannot silently smuggle
    oversized or substituted content past the declared metadata.

`_PENDING_GRANTS` is a process-local in-memory store: acceptable for a
short-lived (default 10 min) grant, but does not survive a restart or
fan out across multiple backend replicas. Tracked as a follow-up (a Redis-
backed store would be a drop-in replacement behind the same three methods)
rather than solved here, matching the plan's explicit MVP scope.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

from app.utils.time_conversion import get_epoch_timestamp_in_ms

from .models import UploadGrant

logger = logging.getLogger(__name__)

__all__ = ["SignedUrlBroker", "GrantExpiredError", "GrantVerificationError"]

DEFAULT_DOWNLOAD_TTL_S = 600
DEFAULT_GRANT_TTL_S = 600

# grant_id -> {artifact_id, document_id, org_id, user_id, declared_size,
#              declared_sha256, mime_type, expires_at}
_PENDING_GRANTS: dict[str, dict[str, Any]] = {}


class GrantExpiredError(Exception):
    """Raised by `commit_version` when the grant has expired or was never
    issued (also returned for an unknown `grant_id`, so a guess costs the
    same as a real expired one)."""


class GrantVerificationError(Exception):
    """Raised by `commit_version` when the actually-uploaded object does
    not match the size/hash declared at grant time."""


class SignedUrlBroker:
    def __init__(self, blob_store: Any, max_bytes: int) -> None:
        self._blob_store = blob_store
        self._max_bytes = max_bytes

    async def get_download_url(
        self, *, org_id: str, document_id: str, version: int | None = None, ttl_s: int = DEFAULT_DOWNLOAD_TTL_S,
    ) -> str:
        """Best-effort TTL: the underlying Node.js signed-URL lifetime is
        controlled server-side by the storage adapter, not by `ttl_s` — see
        that route's own default. Accepted as a parameter here so callers
        can express intent and so a future Node.js change to accept an
        explicit expiry has a call site ready.

        `version` is a storage-layer index, already resolved by the caller
        (`ArtifactRegistryService._resolve_storage_version`) — this broker
        never does registry-to-storage version mapping itself."""
        return await self._blob_store.get_download_url(org_id, document_id, version=version)

    async def get_upload_grant(
        self, *, org_id: str, user_id: str, artifact_id: str, document_id: str,
        declared_size: int, declared_sha256: str, mime_type: str, ttl_s: int = DEFAULT_GRANT_TTL_S,
    ) -> UploadGrant:
        if declared_size > self._max_bytes:
            raise GrantVerificationError(
                f"Declared size {declared_size} exceeds the {self._max_bytes}-byte artifact cap"
            )
        upload_url = await self._blob_store.get_direct_upload_url(org_id, document_id)
        grant_id = str(uuid4())
        expires_at = get_epoch_timestamp_in_ms() + ttl_s * 1000
        _PENDING_GRANTS[grant_id] = {
            "artifact_id": artifact_id, "document_id": document_id, "org_id": org_id,
            "user_id": user_id, "declared_size": declared_size, "declared_sha256": declared_sha256,
            "mime_type": mime_type, "expires_at": expires_at,
        }
        logger.info("Issued upload grant %s for artifact=%s document=%s", grant_id, artifact_id, document_id)
        return UploadGrant(
            grant_id=grant_id, artifact_id=artifact_id, upload_url=upload_url, document_id=document_id,
            declared_size=declared_size, declared_sha256=declared_sha256, expires_at=expires_at,
        )

    def pop_grant(self, grant_id: str, *, org_id: str, user_id: str) -> dict[str, Any]:
        """Consume (single-use) a pending grant, verifying it belongs to
        `org_id`/`user_id` and has not expired. Raises `GrantExpiredError`
        on any mismatch — including wrong org/user, so this doubles as the
        grant-ownership check."""
        grant = _PENDING_GRANTS.pop(grant_id, None)
        if grant is None:
            raise GrantExpiredError(f"Unknown or already-consumed grant: {grant_id}")
        if grant["org_id"] != org_id or grant["user_id"] != user_id:
            raise GrantExpiredError(f"Grant {grant_id} does not belong to this actor")
        if grant["expires_at"] < get_epoch_timestamp_in_ms():
            raise GrantExpiredError(f"Grant {grant_id} has expired")
        return grant

    @staticmethod
    def gc_expired_grants() -> int:
        """Drop expired, never-committed grants. Called from
        `app/sandbox/artifact_cleanup.py`'s periodic loop. Returns the
        number removed."""
        now = get_epoch_timestamp_in_ms()
        expired = [gid for gid, g in _PENDING_GRANTS.items() if g["expires_at"] < now]
        for gid in expired:
            _PENDING_GRANTS.pop(gid, None)
        return len(expired)
