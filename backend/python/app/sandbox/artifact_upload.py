"""Shared artifact upload utilities for sandbox toolsets.

Provides reusable functions for uploading sandbox-produced artifacts
to blob storage, creating ArtifactRecords in ArangoDB with permission
edges, and registering them as background conversation tasks.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Optional
from uuid import uuid4

from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    OriginTypes,
)
from app.models.entities import (
    ArtifactRecord,
    ArtifactType,
    LifecycleStatus,
    RecordType,
)
from app.sandbox.models import ArtifactOutput, ExecutionResult
from app.utils.conversation_tasks import register_task
from app.utils.time_conversion import get_epoch_timestamp_in_ms

logger = logging.getLogger(__name__)

# Hard cap on any single artifact's size when uploaded through the sandbox
# pipeline. A malicious (or runaway) sandbox script can trivially create a
# huge file; we must never slurp it into memory. 25 MiB is generous enough
# for real outputs (multi-page PDFs, large CSVs, PPTX with images) while
# still bounding the backend's exposure.
MAX_ARTIFACT_BYTES = 25 * 1024 * 1024

MIME_TO_ARTIFACT_TYPE: dict[str, ArtifactType] = {
    "image/png": ArtifactType.IMAGE,
    "image/jpeg": ArtifactType.IMAGE,
    "image/gif": ArtifactType.IMAGE,
    "image/svg+xml": ArtifactType.IMAGE,
    "image/webp": ArtifactType.IMAGE,
    "application/pdf": ArtifactType.DOCUMENT,
    "text/csv": ArtifactType.SPREADSHEET,
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ArtifactType.SPREADSHEET,
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ArtifactType.DOCUMENT,
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ArtifactType.PRESENTATION,
    "text/html": ArtifactType.DOCUMENT,
    "text/markdown": ArtifactType.DOCUMENT,
    "application/json": ArtifactType.DATA_FILE,
}


def _infer_artifact_type(mime_type: str) -> ArtifactType:
    return MIME_TO_ARTIFACT_TYPE.get(mime_type, ArtifactType.OTHER)


async def create_artifact_record(
    *,
    graph_provider: Any,
    document_id: str,
    file_name: str,
    mime_type: str,
    size_bytes: int,
    org_id: str,
    user_id: str,
    conversation_id: str,
    connector_name: Connectors = Connectors.CODING_SANDBOX,
    source_tool: str | None = None,
) -> str:
    """Create an ArtifactRecord in ArangoDB with permission edges.

    Returns the record ID (``_key``) of the created record.
    """
    record_id = str(uuid4())
    now = get_epoch_timestamp_in_ms()

    # user_id is the external userId field; resolve to the _key used in edges
    user_doc = await graph_provider.get_user_by_user_id(user_id)
    if not user_doc:
        raise ValueError(f"User not found for userId: {user_id}")
    user_key = user_doc.get("_key") or user_doc.get("id")

    artifact = ArtifactRecord(
        id=record_id,
        org_id=org_id,
        record_name=file_name,
        record_type=RecordType.ARTIFACT,
        external_record_id=document_id,
        version=1,
        origin=OriginTypes.UPLOAD,
        connector_name=connector_name,
        connector_id=f"{connector_name.value.lower()}_{org_id}",
        mime_type=mime_type,
        size_in_bytes=size_bytes,
        created_at=now,
        updated_at=now,
        indexing_status="NOT_STARTED",
        extraction_status="NOT_STARTED",
        preview_renderable=True,
        hide_weburl=True,
        artifact_type=_infer_artifact_type(mime_type),
        lifecycle_status=LifecycleStatus.PUBLISHED,
        source_tool=source_tool,
        conversation_id=conversation_id,
    )

    record_data = artifact.to_arango_base_record()
    artifact_data = artifact.to_arango_artifact_record()

    permission_edge = {
        "from_id": user_key,
        "from_collection": CollectionNames.USERS.value,
        "to_id": record_id,
        "to_collection": CollectionNames.RECORDS.value,
        "type": "USER",
        "role": "OWNER",
        "createdAtTimestamp": now,
        "updatedAtTimestamp": now,
    }

    is_of_type_edge = {
        "from_id": record_id,
        "from_collection": CollectionNames.RECORDS.value,
        "to_id": record_id,
        "to_collection": CollectionNames.ARTIFACTS.value,
        "createdAtTimestamp": now,
        "updatedAtTimestamp": now,
    }

    await graph_provider.batch_upsert_nodes(
        [record_data], CollectionNames.RECORDS.value,
    )
    await graph_provider.batch_upsert_nodes(
        [artifact_data], CollectionNames.ARTIFACTS.value,
    )
    await graph_provider.batch_create_edges(
        [permission_edge], CollectionNames.PERMISSION.value,
    )
    await graph_provider.batch_create_edges(
        [is_of_type_edge], CollectionNames.IS_OF_TYPE.value,
    )

    logger.info(
        "Created ArtifactRecord %s for document %s (user=%s, conversation=%s)",
        record_id, document_id, user_id, conversation_id,
    )
    return record_id


async def upload_bytes_artifact(
    *,
    file_name: str,
    file_bytes: bytes,
    mime_type: str,
    blob_store: Any,
    org_id: str,
    conversation_id: str,
    user_id: str | None = None,
    graph_provider: Any = None,
    connector_name: Connectors = Connectors.CODING_SANDBOX,
    source_tool: str | None = None,
) -> dict[str, Any] | None:
    """Upload an in-memory artifact (already produced bytes) to blob storage.

    Mirrors the single-file branch of :func:`upload_artifacts_to_blob` but
    skips the on-disk ``_read_file_bytes`` path since the bytes are already
    produced server-side (no sandbox-root check needed).

    When ``user_id`` and ``graph_provider`` are provided, also creates an
    ``ArtifactRecord`` in ArangoDB with permission edges.

    Returns the upload-info dict (``fileName``, ``signedUrl``/``downloadUrl``,
    ``mimeType``, ``sizeBytes``, optional ``recordId``), or ``None`` on
    failure (including when ``file_bytes`` exceeds ``MAX_ARTIFACT_BYTES``).
    """
    if len(file_bytes) > MAX_ARTIFACT_BYTES:
        logger.warning(
            "Refusing to upload in-memory artifact %s: size %d exceeds cap %d",
            file_name, len(file_bytes), MAX_ARTIFACT_BYTES,
        )
        return None
    try:
        upload_info = await blob_store.save_conversation_file_to_storage(
            org_id=org_id,
            conversation_id=conversation_id,
            file_name=file_name,
            file_bytes=file_bytes,
            content_type=mime_type,
        )
    except Exception:
        logger.exception("Failed to save bytes artifact %s to blob", file_name)
        return None

    result_entry: dict[str, Any] = {
        **upload_info,
        "mimeType": mime_type,
        "sizeBytes": len(file_bytes),
    }

    if user_id and graph_provider:
        document_id = upload_info.get("documentId", "")
        if document_id:
            try:
                record_id = await create_artifact_record(
                    graph_provider=graph_provider,
                    document_id=document_id,
                    file_name=file_name,
                    mime_type=mime_type,
                    size_bytes=len(file_bytes),
                    org_id=org_id,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    connector_name=connector_name,
                    source_tool=source_tool,
                )
                result_entry["recordId"] = record_id
            except Exception:
                logger.exception(
                    "Failed to create ArtifactRecord for %s", file_name,
                )

    return result_entry


async def upload_artifacts_to_blob(
    artifacts: list[ArtifactOutput],
    *,
    blob_store: Any,
    org_id: str,
    conversation_id: str,
    user_id: str | None = None,
    graph_provider: Any = None,
    connector_name: Connectors = Connectors.CODING_SANDBOX,
    source_tool: str | None = None,
) -> list[dict[str, Any]]:
    """Upload a list of ArtifactOutput files to blob storage.

    When ``user_id`` and ``graph_provider`` are provided, also creates
    ArtifactRecords in ArangoDB with permission edges.

    Returns a list of upload info dicts, each containing
    ``fileName``, ``mimeType``, ``sizeBytes``, ``signedUrl``/``downloadUrl``,
    and optionally ``recordId``.
    """
    uploaded: list[dict[str, Any]] = []

    for artifact in artifacts:
        try:
            file_bytes = _read_file_bytes(artifact.file_path)
            if file_bytes is None:
                continue

            upload_info = await blob_store.save_conversation_file_to_storage(
                org_id=org_id,
                conversation_id=conversation_id,
                file_name=artifact.file_name,
                file_bytes=file_bytes,
                content_type=artifact.mime_type,
            )

            result_entry: dict[str, Any] = {
                **upload_info,
                "mimeType": artifact.mime_type,
                "sizeBytes": artifact.size_bytes,
            }

            if user_id and graph_provider:
                document_id = upload_info.get("documentId", "")
                if document_id:
                    try:
                        record_id = await create_artifact_record(
                            graph_provider=graph_provider,
                            document_id=document_id,
                            file_name=artifact.file_name,
                            mime_type=artifact.mime_type,
                            size_bytes=artifact.size_bytes,
                            org_id=org_id,
                            user_id=user_id,
                            conversation_id=conversation_id,
                            connector_name=connector_name,
                            source_tool=source_tool,
                        )
                        result_entry["recordId"] = record_id
                    except Exception:
                        logger.exception(
                            "Failed to create ArtifactRecord for %s", artifact.file_name,
                        )

            uploaded.append(result_entry)
        except Exception:
            logger.exception("Failed to upload artifact %s", artifact.file_name)

    return uploaded


def schedule_artifact_upload_task(
    exec_result: ExecutionResult,
    *,
    blob_store: Any,
    org_id: str,
    conversation_id: str,
    user_id: str | None = None,
    config_service: Any = None,
    graph_provider: Any = None,
    connector_name: Connectors = Connectors.CODING_SANDBOX,
    source_tool: str | None = None,
) -> None:
    """Schedule artifact uploads as a background conversation task.

    If ``blob_store`` is None, attempts to create one from ``config_service``
    and ``graph_provider``.
    """
    if not exec_result.artifacts or not conversation_id:
        return

    async def _upload() -> Optional[dict[str, Any]]:
        try:
            store = blob_store
            if store is None and config_service and graph_provider:
                from app.modules.transformers.blob_storage import BlobStorage
                store = BlobStorage(
                    logger=logger,
                    config_service=config_service,
                    graph_provider=graph_provider,
                )
            if store is None:
                logger.warning("No blob store available for artifact upload")
                return None

            results = await upload_artifacts_to_blob(
                exec_result.artifacts,
                blob_store=store,
                org_id=org_id,
                conversation_id=conversation_id,
                user_id=user_id,
                graph_provider=graph_provider,
                connector_name=connector_name,
                source_tool=source_tool,
            )
            if results:
                return {"type": "artifacts", "artifacts": results}
        except Exception:
            logger.exception("Background artifact upload failed")
        return None

    task = asyncio.create_task(_upload())
    register_task(conversation_id, task)


def _read_file_bytes(path: str, max_bytes: int = MAX_ARTIFACT_BYTES) -> bytes | None:
    """Read a file, validating it lives under an expected sandbox root.

    The file is streamed in chunks and bailed out if it exceeds ``max_bytes``
    so a malicious sandbox script cannot OOM the backend by producing a huge
    artifact. Returns ``None`` if the file is outside the sandbox roots, not
    readable, or exceeds the size cap.
    """
    import tempfile

    tmp = os.path.realpath(tempfile.gettempdir())
    sandbox_roots = (
        os.path.realpath(os.path.join(tmp, "pipeshub_sandbox")),
        os.path.realpath(os.path.join(tmp, "pipeshub_sandbox_docker")),
    )
    resolved = os.path.realpath(path)
    if not any(
        resolved.startswith(root + os.sep) or resolved == root
        for root in sandbox_roots
    ):
        logger.warning("Refusing to read file outside sandbox roots: %s", path)
        return None

    try:
        # Early reject based on stat() so we never even open a huge file.
        st_size = os.path.getsize(resolved)
        if st_size > max_bytes:
            logger.warning(
                "Artifact %s exceeds size cap (%d > %d bytes); skipping",
                path, st_size, max_bytes,
            )
            return None
    except OSError:
        logger.warning("Could not stat artifact file: %s", path)
        return None

    try:
        # Stream in chunks; double-check the running total in case the file
        # grew between stat() and read() (TOCTOU).
        buf = bytearray()
        with open(resolved, "rb") as f:
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                if len(buf) + len(chunk) > max_bytes:
                    logger.warning(
                        "Artifact %s grew past size cap during read; skipping",
                        path,
                    )
                    return None
                buf.extend(chunk)
        return bytes(buf)
    except OSError:
        logger.warning("Could not read artifact file: %s", path)
        return None
