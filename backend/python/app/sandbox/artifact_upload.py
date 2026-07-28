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

from app.config.constants.arangodb import Connectors
from app.models.entities import ArtifactType
from app.sandbox.models import ArtifactOutput, ExecutionResult
from app.utils.conversation_tasks import register_task

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


def infer_artifact_type(mime_type: str) -> ArtifactType:
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
    """Create an ArtifactRecord in ArangoDB with permission edges, for a
    blob the caller already uploaded elsewhere (e.g. `database_sandbox.py`'s
    CSV export).

    Thin wrapper over `ArtifactRegistryService.register_existing` (see
    `app/services/artifact_registry/`) — kept as a free function with this
    EXACT signature so every existing call site keeps working unchanged
    while the actual record/permission-edge writes flow through the same
    single registry every other artifact producer now uses.

    Returns the record ID (``_key``) of the created record.
    """
    from app.services.artifact_registry import Actor, ArtifactRegistryService

    registry = ArtifactRegistryService(graph_provider, blob_store=None)
    actor = Actor(org_id=org_id, user_id=user_id)
    metadata = await registry.register_existing(
        actor=actor,
        document_id=document_id,
        name=file_name,
        artifact_type=infer_artifact_type(mime_type),
        mime_type=mime_type,
        size_bytes=size_bytes,
        conversation_id=conversation_id,
        connector_name=connector_name,
        source_tool=source_tool,
    )
    logger.info(
        "Created ArtifactRecord %s for document %s (user=%s, conversation=%s)",
        metadata.artifact_id, document_id, user_id, conversation_id,
    )
    return metadata.artifact_id


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

    When ``user_id`` and ``graph_provider`` are provided, routes through
    `ArtifactRegistryService.register_output` (see
    `app/services/artifact_registry/`) instead of the old flat, always-new,
    never-versioned upload — a call with a file name that already exists as
    an artifact in this conversation now bumps that artifact's version
    instead of creating a disconnected duplicate (this is a semantic
    upgrade for every existing caller — `image_generator`/`database_sandbox`
    — with no call-site change). Without both, falls back to a plain,
    unregistered blob upload exactly as before (used by tests/callers with
    no graph access).

    Returns the upload-info dict (``fileName``, ``documentId``,
    ``downloadUrl``, ``mimeType``, ``sizeBytes``, optional ``recordId``/
    ``version``), or ``None`` on failure (including when ``file_bytes``
    exceeds ``MAX_ARTIFACT_BYTES``).
    """
    if len(file_bytes) > MAX_ARTIFACT_BYTES:
        logger.warning(
            "Refusing to upload in-memory artifact %s: size %d exceeds cap %d",
            file_name, len(file_bytes), MAX_ARTIFACT_BYTES,
        )
        return None

    if user_id and graph_provider:
        from app.services.artifact_registry import Actor, ArtifactRegistryService

        registry = ArtifactRegistryService(graph_provider, blob_store)
        actor = Actor(org_id=org_id, user_id=user_id)
        try:
            metadata, version = await registry.register_output(
                actor=actor,
                name=file_name,
                artifact_type=infer_artifact_type(mime_type),
                mime_type=mime_type,
                content=file_bytes,
                conversation_id=conversation_id,
                source_tool=source_tool,
                connector_name=connector_name,
            )
        except Exception:
            logger.exception("Failed to register artifact %s via registry", file_name)
            return None

        result_entry: dict[str, Any] = {
            "documentId": metadata.document_id,
            "fileName": metadata.name,
            "mimeType": metadata.mime_type,
            "sizeBytes": metadata.size_bytes,
            "recordId": metadata.artifact_id,
            "version": metadata.version,
            "artifactType": metadata.artifact_type.value,
        }
        if version is not None and version.deduplicated:
            result_entry["deduplicated"] = True
        try:
            result_entry["downloadUrl"] = await registry.get_download_url(actor=actor, artifact_id=metadata.artifact_id)
        except Exception:
            logger.warning("Failed to obtain download URL for artifact %s", metadata.artifact_id, exc_info=True)
        return result_entry

    # No graph access — plain, unregistered blob upload (legacy behavior).
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

    return {
        **upload_info,
        "mimeType": mime_type,
        "sizeBytes": len(file_bytes),
    }


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
                "artifactType": infer_artifact_type(artifact.mime_type).value,
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
