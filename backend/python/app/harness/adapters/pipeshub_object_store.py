from __future__ import annotations

import hashlib
from typing import Protocol

import aiohttp

from app.exceptions.storage_exceptions import StorageRequestRejectedError
from app.harness.contracts.workspace import ObjectRef, ObjectScope
from app.modules.transformers.blob_storage import StorageDocumentNotFoundError
from harness_core.workspace.types import ObjectStoreUnavailable


class PipesHubBlobGateway(Protocol):
    async def save_binary_to_storage(
        self,
        org_id: str,
        record_id: str,
        file_name: str,
        extension: str,
        content_type: str,
        binary_data: bytes,
    ) -> tuple[str | None, int | None]: ...

    async def read_binary_from_storage(
        self,
        org_id: str,
        document_id: str,
        *,
        version: int | None = None,
    ) -> bytes: ...


class PipesHubBinaryObjectStore:
    """Binary CAS port backed by PipesHub's configured local/S3/Azure store.

    Object references contain no authority. Every operation carries the
    server-derived organization scope separately, and reads verify both hash
    and size before bytes can enter a workspace.
    """

    def __init__(self, gateway: PipesHubBlobGateway) -> None:
        self._gateway = gateway

    async def content_hash(self, scope: ObjectScope, content: bytes) -> str:
        del scope  # plain sha256 in every scope
        return f"sha256:{hashlib.sha256(content).hexdigest()}"

    async def put(self, scope: ObjectScope, content: bytes) -> ObjectRef:
        immutable = bytes(content)
        digest = hashlib.sha256(immutable).hexdigest()
        content_hash = f"sha256:{digest}"
        document_id, stored_size = await self._gateway.save_binary_to_storage(
            scope.tenant_id,
            f"agent-harness-object-{digest}",
            f"{digest}.bin",
            "bin",
            "application/octet-stream",
            immutable,
        )
        if not document_id or stored_size != len(immutable):
            # The gateway logs and swallows its own failures (an outage, a refused write) as ``(None, None)``;
            # a content-addressed put is safe to repeat, so the caller waits and tries again (CH-09).
            raise ObjectStoreUnavailable("PipesHub storage did not persist the binary object")
        return ObjectRef(
            object_id=document_id,
            content_hash=content_hash,
            size_bytes=len(immutable),
        )

    async def get(self, scope: ObjectScope, ref: ObjectRef) -> bytes:
        try:
            content = await self._gateway.read_binary_from_storage(
                scope.tenant_id,
                ref.object_id,
            )
        except (StorageDocumentNotFoundError, StorageRequestRejectedError):
            raise
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise ObjectStoreUnavailable(f"PipesHub storage could not be read: {type(exc).__name__}") from exc
        if len(content) != ref.size_bytes:
            raise ValueError("Object content size does not match its immutable reference")
        digest = f"sha256:{hashlib.sha256(content).hexdigest()}"
        if digest != ref.content_hash:
            raise ValueError("Object content hash does not match its immutable reference")
        return content
