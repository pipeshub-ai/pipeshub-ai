"""`VersionManager` — the SINGLE writer for an artifact's blob content +
version bookkeeping across MongoDB (via `BlobStorage`) and the graph DB
(`records`/`artifacts` collections).

Centralizing this fixes the pre-existing divergence risk called out in the
plan: previously nothing guaranteed a blob version bump and a
`record.version` bump happened together. Every write here does
blob-then-graph in that order (never the reverse — an orphaned blob version
with no graph pointer is harmless dead storage; a graph version bump with
no matching blob would serve wrong/missing content) and treats a
post-blob-write graph failure as `PENDING_RECONCILE` rather than silently
losing the update — see `mark_pending_reconcile`/`app/sandbox/artifact_cleanup.py`.
"""

from __future__ import annotations

import hashlib
import logging
import os
from typing import Any
from uuid import uuid4

from app.config.constants.arangodb import CollectionNames, Connectors, OriginTypes
from app.models.entities import (
    ArtifactRecord,
    ArtifactType,
    LifecycleStatus,
    RecordType,
    deserialize_artifact_versions,
    serialize_artifact_versions,
)
from app.utils.time_conversion import get_epoch_timestamp_in_ms

from .access import AccessPolicy
from .models import Actor, ArtifactMetadata, ArtifactVersion

logger = logging.getLogger(__name__)

__all__ = [
    "VersionManager",
    "VersionConflictError",
    "VersionSyncError",
    "VersionMappingNotFoundError",
    "PENDING_RECONCILE_REASON",
    "compute_content_hash",
    "resolve_storage_version",
    "to_metadata",
    "to_metadata_from_docs",
]

# Stashed in `records.reason` when the blob write for a version succeeded
# but the graph-side bookkeeping update failed — `artifact_cleanup.py`'s
# periodic loop scans for this marker and retries the graph update from
# the blob's now-authoritative state. Never surfaced to the model.
PENDING_RECONCILE_REASON = "ARTIFACT_VERSION_PENDING_RECONCILE"


class VersionConflictError(Exception):
    """Raised by `add_version` when the caller's `expected_version` no
    longer matches the artifact's current version — a concurrent writer
    won the race. The caller (a tool) should re-fetch and retry, not
    silently overwrite."""


class VersionSyncError(Exception):
    """Raised when the blob write for a new version succeeded but the
    graph-side version bump failed. The blob content is durable (a new
    version exists in storage) but the artifact record still reports the
    OLD version until reconciliation runs — never reported to the caller
    as success."""


class VersionMappingNotFoundError(Exception):
    """Raised by `resolve_storage_version` when the requested registry
    `version` is neither the artifact's current version nor present in its
    `versions` bookkeeping (pre-migration artifact, or an unknown/future
    version). Callers must treat this as "not found", never fall back to
    serving the wrong bytes."""


def compute_content_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def resolve_storage_version(current_version: int, versions: list[dict], version: int | None) -> int | None:
    """Map a registry `version` number to the storage layer's
    `versionHistory` index, or `None` to mean "current" (skip `?version=`
    entirely — the fast path storage already optimizes).

    Pure/no-I/O so both `ArtifactRegistryService._resolve_storage_version`
    (which fetches `versions` from the graph) and the connectors' stream
    route (which already has an `ArtifactRecord` with `.version`/`.versions`
    in hand from `get_record_by_id`) share ONE mapping decision — see the
    plan's explicit "reuse Phase 2's lookup; do NOT re-implement the
    mapping" note.
    """
    if version is None or version == current_version:
        return None
    for entry in versions:
        if entry.get("registryVersion") == version:
            return entry.get("storageVersion")
    raise VersionMappingNotFoundError(
        f"Version {version} has no recorded storage mapping (current version is {current_version})"
    )


class VersionManager:
    def __init__(self, graph_provider: Any, blob_store: Any, access_policy: AccessPolicy) -> None:
        self._graph_provider = graph_provider
        self._blob_store = blob_store
        self._access = access_policy

    async def create(
        self,
        *,
        actor: Actor,
        name: str,
        artifact_type: ArtifactType,
        mime_type: str,
        content: bytes,
        conversation_id: str | None,
        description: str = "",
        logical_name: str | None = None,
        source_tool: str | None = None,
        is_temporary: bool = False,
        connector_name: Connectors = Connectors.CODING_SANDBOX,
        result_schema: dict | None = None,
    ) -> ArtifactMetadata:
        """Create a brand-new, version-1 artifact: blob doc (version-enabled)
        + `records`/`artifacts` graph docs + owner permission edge, all
        written together so a partial failure never leaves an orphaned
        permission-less record."""
        content_hash = compute_content_hash(content)
        upload_info = await self._blob_store.save_versioned_artifact_to_storage(
            org_id=actor.org_id,
            conversation_id=conversation_id or "unscoped",
            file_name=name,
            file_bytes=content,
            content_type=mime_type,
        )
        document_id = upload_info.get("documentId")
        if not document_id:
            raise VersionSyncError(f"Blob upload for artifact {name!r} returned no documentId")

        artifact_id = str(uuid4())
        now = get_epoch_timestamp_in_ms()
        record = ArtifactRecord(
            id=artifact_id,
            org_id=actor.org_id,
            record_name=name,
            record_type=RecordType.ARTIFACT,
            external_record_id=str(document_id),
            version=1,
            origin=OriginTypes.UPLOAD,
            connector_name=connector_name,
            connector_id=f"{connector_name.value.lower()}_{actor.org_id}",
            mime_type=mime_type,
            size_in_bytes=len(content),
            created_at=now,
            updated_at=now,
            indexing_status="NOT_STARTED",
            extraction_status="NOT_STARTED",
            preview_renderable=True,
            hide_weburl=True,
            artifact_type=artifact_type,
            lifecycle_status=LifecycleStatus.PUBLISHED,
            source_tool=source_tool,
            conversation_id=conversation_id,
            is_temporary=is_temporary,
            logical_name=logical_name or name,
            content_hash=content_hash,
            result_schema=result_schema,
        )

        permission_edge = await self._access.grant_owner_permission(actor, artifact_id, now=now)
        is_of_type_edge = {
            "from_id": artifact_id,
            "from_collection": CollectionNames.RECORDS.value,
            "to_id": artifact_id,
            "to_collection": CollectionNames.ARTIFACTS.value,
            "createdAtTimestamp": now,
            "updatedAtTimestamp": now,
        }

        await self._graph_provider.batch_upsert_nodes([record.to_arango_base_record()], CollectionNames.RECORDS.value)
        await self._graph_provider.batch_upsert_nodes([record.to_arango_artifact_record()], CollectionNames.ARTIFACTS.value)
        await self._graph_provider.batch_create_edges([permission_edge], CollectionNames.PERMISSION.value)
        await self._graph_provider.batch_create_edges([is_of_type_edge], CollectionNames.IS_OF_TYPE.value)

        logger.info(
            "Created artifact %s (name=%r type=%s conversation=%s user=%s)",
            artifact_id, name, artifact_type.value, conversation_id, actor.user_id,
        )
        return to_metadata(record, document_id=str(document_id))

    async def create_from_existing_document(
        self,
        *,
        actor: Actor,
        document_id: str,
        name: str,
        artifact_type: ArtifactType,
        mime_type: str,
        size_bytes: int,
        conversation_id: str | None,
        description: str = "",
        logical_name: str | None = None,
        source_tool: str | None = None,
        content_hash: str | None = None,
        connector_name: Connectors = Connectors.CODING_SANDBOX,
    ) -> ArtifactMetadata:
        """Register a graph-side artifact record for a blob that was ALREADY
        uploaded elsewhere (e.g. `database_sandbox.py`'s CSV export, which
        calls `BlobStorage.save_conversation_file_to_storage` itself before
        this). Skips the blob write `create()` does — this is the one
        legitimate reason to bypass it, since the bytes are already
        durable — but writes the SAME record/artifact/permission-edge
        triple, so every artifact is queryable/listable through this one
        registry regardless of which producer created it (see plan's
        "unify producers")."""
        artifact_id = str(uuid4())
        now = get_epoch_timestamp_in_ms()
        record = ArtifactRecord(
            id=artifact_id,
            org_id=actor.org_id,
            record_name=name,
            record_type=RecordType.ARTIFACT,
            external_record_id=str(document_id),
            version=1,
            origin=OriginTypes.UPLOAD,
            connector_name=connector_name,
            connector_id=f"{connector_name.value.lower()}_{actor.org_id}",
            mime_type=mime_type,
            size_in_bytes=size_bytes,
            created_at=now,
            updated_at=now,
            indexing_status="NOT_STARTED",
            extraction_status="NOT_STARTED",
            preview_renderable=True,
            hide_weburl=True,
            artifact_type=artifact_type,
            lifecycle_status=LifecycleStatus.PUBLISHED,
            source_tool=source_tool,
            conversation_id=conversation_id,
            logical_name=logical_name or name,
            content_hash=content_hash,
        )
        permission_edge = await self._access.grant_owner_permission(actor, artifact_id, now=now)
        is_of_type_edge = {
            "from_id": artifact_id,
            "from_collection": CollectionNames.RECORDS.value,
            "to_id": artifact_id,
            "to_collection": CollectionNames.ARTIFACTS.value,
            "createdAtTimestamp": now,
            "updatedAtTimestamp": now,
        }
        await self._graph_provider.batch_upsert_nodes([record.to_arango_base_record()], CollectionNames.RECORDS.value)
        await self._graph_provider.batch_upsert_nodes([record.to_arango_artifact_record()], CollectionNames.ARTIFACTS.value)
        await self._graph_provider.batch_create_edges([permission_edge], CollectionNames.PERMISSION.value)
        await self._graph_provider.batch_create_edges([is_of_type_edge], CollectionNames.IS_OF_TYPE.value)
        return to_metadata(record, document_id=str(document_id))

    async def add_version(
        self,
        *,
        actor: Actor,
        artifact_id: str,
        content: bytes,
        mime_type: str | None = None,
        expected_version: int | None = None,
    ) -> tuple[ArtifactVersion, ArtifactMetadata]:
        """Bump `artifact_id` to a new version, or return the current
        version unchanged (`ArtifactVersion.deduplicated=True`) when
        `content`'s hash matches what's already stored — makes re-running
        the same code idempotent instead of accumulating no-op versions."""
        record = await self._access.authorize_write(actor, artifact_id)
        artifact_doc = await self._graph_provider.get_document(artifact_id, CollectionNames.ARTIFACTS.value)
        if not artifact_doc:
            from .access import ArtifactNotFoundError
            raise ArtifactNotFoundError(f"Artifact metadata missing for: {artifact_id}")

        current_version = int(record.get("version", 1))
        if expected_version is not None and expected_version != current_version:
            raise VersionConflictError(
                f"Artifact {artifact_id} is at version {current_version}, "
                f"but caller expected {expected_version} — reload and retry."
            )

        content_hash = compute_content_hash(content)
        effective_mime = mime_type or record.get("mimeType") or "application/octet-stream"
        now = get_epoch_timestamp_in_ms()

        if artifact_doc.get("contentHash") == content_hash:
            version = ArtifactVersion(
                version=current_version, size_bytes=len(content), content_hash=content_hash,
                mime_type=effective_mime, created_at=now, created_by_user_id=actor.user_id,
                deduplicated=True,
            )
            return version, to_metadata_from_docs(record, artifact_doc)

        document_id = record.get("externalRecordId")
        file_name = record.get("recordName") or artifact_doc.get("name") or artifact_id
        upload_result = await self._blob_store.upload_artifact_version(
            org_id=actor.org_id,
            document_id=document_id,
            file_name=file_name,
            file_bytes=content,
            content_type=effective_mime,
        )

        new_version = current_version + 1
        existing_versions: list[dict] = deserialize_artifact_versions(artifact_doc.get("versions"))
        new_versions = self._append_version_bookkeeping(
            existing_versions,
            current_version=current_version,
            new_version=new_version,
            upload_result=upload_result,
            prior_content_hash=artifact_doc.get("contentHash"),
            prior_size_bytes=artifact_doc.get("sizeInBytes"),
            prior_created_at=record.get("createdAtTimestamp") or now,
            new_content_hash=content_hash,
            new_size_bytes=len(content),
            now=now,
        )
        try:
            await self._graph_provider.update_node(
                artifact_id, CollectionNames.RECORDS.value,
                {"version": new_version, "sizeInBytes": len(content), "updatedAtTimestamp": now,
                 "isLatestVersion": True, "mimeType": effective_mime, "reason": None},
            )
            await self._graph_provider.update_node(
                artifact_id, CollectionNames.ARTIFACTS.value,
                {"contentHash": content_hash, "sizeInBytes": len(content), "mimeType": effective_mime,
                 "versions": serialize_artifact_versions(new_versions)},
            )
        except Exception:
            logger.critical(
                "PENDING_RECONCILE: blob version write for artifact=%s document=%s "
                "succeeded (new_version=%d hash=%s) but graph update failed — "
                "artifact_cleanup.py must reconcile this.",
                artifact_id, document_id, new_version, content_hash, exc_info=True,
            )
            try:
                await self._graph_provider.update_node(
                    artifact_id, CollectionNames.RECORDS.value, {"reason": PENDING_RECONCILE_REASON},
                )
            except Exception:
                logger.critical("Failed to even mark artifact %s as PENDING_RECONCILE", artifact_id, exc_info=True)
            raise VersionSyncError(
                f"Version {new_version} was uploaded for artifact {artifact_id} but graph "
                "bookkeeping failed to update — the artifact may report a stale version "
                "until reconciled."
            ) from None

        record = {**record, "version": new_version, "sizeInBytes": len(content), "mimeType": effective_mime}
        artifact_doc = {**artifact_doc, "contentHash": content_hash, "sizeInBytes": len(content),
                         "mimeType": effective_mime, "versions": serialize_artifact_versions(new_versions)}
        version = ArtifactVersion(
            version=new_version, size_bytes=len(content), content_hash=content_hash,
            mime_type=effective_mime, created_at=now, created_by_user_id=actor.user_id,
        )
        logger.info("Artifact %s bumped to version %d by user=%s", artifact_id, new_version, actor.user_id)
        return version, to_metadata_from_docs(record, artifact_doc)

    @staticmethod
    def _append_version_bookkeeping(
        existing_versions: list[dict],
        *,
        current_version: int,
        new_version: int,
        upload_result: dict[str, Any],
        prior_content_hash: str | None,
        prior_size_bytes: int | None,
        prior_created_at: int,
        new_content_hash: str,
        new_size_bytes: int,
        now: int,
    ) -> list[dict]:
        """Build the updated `versions` bookkeeping list for one `add_version`
        call. Storage indices come ONLY from `upload_result` (what Node's
        `uploadNextVersionDocument` response actually reported) — never
        computed from `new_version`/`len(existing_versions)`, since Node's
        `versionHistory` numbering is not guaranteed to track the registry's
        1-based version 1:1 (see module docstring)."""
        versions = list(existing_versions)
        storage_version = upload_result.get("storageVersion")
        prior_storage_version = upload_result.get("priorStorageVersion")

        # First-ever bump for this artifact: Node lazily snapshots the
        # pre-existing "current" content as an extra versionHistory entry
        # before writing the new bytes. Backfill version 1's mapping now —
        # it never got one at create() time because no blob version existed
        # yet to point at.
        if not versions and prior_storage_version is not None:
            versions.append({
                "registryVersion": current_version,
                "storageVersion": prior_storage_version,
                "contentHash": prior_content_hash,
                "sizeBytes": prior_size_bytes,
                "createdAt": prior_created_at,
            })

        if storage_version is not None:
            versions.append({
                "registryVersion": new_version,
                "storageVersion": storage_version,
                "contentHash": new_content_hash,
                "sizeBytes": new_size_bytes,
                "createdAt": now,
            })
        else:
            logger.warning(
                "add_version: no storageVersion reported for registryVersion=%d — "
                "version-pinned retrieval for this version will fall back to latest.",
                new_version,
            )
        return versions


def to_metadata(record: ArtifactRecord, *, document_id: str) -> ArtifactMetadata:
    return ArtifactMetadata(
        artifact_id=record.id,
        org_id=record.org_id,
        conversation_id=record.conversation_id,
        name=record.record_name,
        logical_name=record.logical_name or record.record_name,
        artifact_type=record.artifact_type,
        mime_type=record.mime_type,
        description=record.description,
        lifecycle_status=record.lifecycle_status,
        version=record.version,
        size_bytes=record.size_in_bytes or 0,
        content_hash=record.content_hash,
        source_tool=record.source_tool,
        document_id=document_id,
        result_schema=record.result_schema,
        is_temporary=record.is_temporary,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def to_metadata_from_docs(record: dict, artifact_doc: dict) -> ArtifactMetadata:
    _, ext = os.path.splitext(record.get("recordName") or "")
    return ArtifactMetadata(
        artifact_id=record.get("id") or record.get("_key"),
        org_id=record.get("orgId"),
        conversation_id=artifact_doc.get("conversationId"),
        name=record.get("recordName"),
        logical_name=artifact_doc.get("logicalName") or record.get("recordName"),
        artifact_type=ArtifactType(artifact_doc.get("artifactType") or ArtifactType.OTHER.value),
        mime_type=record.get("mimeType") or "application/octet-stream",
        description=artifact_doc.get("description") or "",
        lifecycle_status=LifecycleStatus(artifact_doc.get("lifecycleStatus") or LifecycleStatus.PUBLISHED.value),
        version=record.get("version", 1),
        size_bytes=record.get("sizeInBytes") or 0,
        content_hash=artifact_doc.get("contentHash"),
        source_tool=artifact_doc.get("sourceTool"),
        document_id=record.get("externalRecordId"),
        result_schema=artifact_doc.get("resultSchema"),
        is_temporary=bool(artifact_doc.get("isTemporary", False)),
        created_at=record.get("createdAtTimestamp"),
        updated_at=record.get("updatedAtTimestamp"),
    )
