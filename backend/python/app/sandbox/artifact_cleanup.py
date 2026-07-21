"""Periodic cleanup of temporary sandbox artifacts.

Runs as a background asyncio task, cleaning up:
1. Local temp directories older than the configured TTL.
2. Artifacts stuck in `PENDING_RECONCILE` (blob version write succeeded,
   graph-side bookkeeping update failed) — see `reconcile_pending_artifacts`.

Configuration via environment variables:
- ARTIFACT_TEMP_TTL_HOURS: Hours before a temp directory is deleted (default: 1)
- ARTIFACT_CLEANUP_INTERVAL_MINUTES: Minutes between cleanup runs (default: 30)
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import time
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_TTL_HOURS = 1
DEFAULT_INTERVAL_MINUTES = 30

_cleanup_task: asyncio.Task | None = None
# Cap per sweep — this is a slow-drip repair path (a rare failure mode),
# not a bulk migration; an unbounded scan could stall the cleanup loop.
_MAX_RECONCILE_PER_SWEEP = 200


async def reconcile_pending_artifacts(graph_provider: Any, blob_store: Any) -> int:
    """Repair artifacts left in `PENDING_RECONCILE` by `VersionManager.add_version`
    — the blob write for a version bump succeeded, but the graph-side
    version bump failed right after, so the `records`/`artifacts` docs still
    show the OLD version/size/hash. The blob layer's `versionHistory` is the
    ground truth here (the write DID happen), so this pass:

    1. Finds every `records` doc with `reason == PENDING_RECONCILE_REASON`.
    2. Re-downloads the CURRENT bytes for that artifact's document and
       re-hashes them (the graph never got the new hash, and there is no
       other authoritative source for it).
    3. Reads the storage `versionHistory` to recover the storage index the
       failed bump landed at, and bumps `records.version` by exactly one —
       matching what `add_version` intended before it failed. Also stamps
       `versions` bookkeeping so a subsequent `get_content(version=N)` call
       for the now-reconciled version resolves.
    4. Clears `reason` only after both graph writes succeed, so a second
       failure leaves the marker in place for the next sweep instead of
       silently giving up.

    Best-effort: assumes no OTHER version bump raced in between the
    original failure and this reconcile (writes to one artifact are not
    expected to be concurrent in practice — see `fix-version-races`).
    Returns the number of artifacts successfully reconciled.
    """
    from app.config.constants.arangodb import CollectionNames
    from app.models.entities import deserialize_artifact_versions, serialize_artifact_versions
    from app.services.artifact_registry.versioning import (
        PENDING_RECONCILE_REASON,
        compute_content_hash,
    )

    try:
        pending = await graph_provider.get_documents_paginated(
            CollectionNames.RECORDS.value, skip=0, limit=_MAX_RECONCILE_PER_SWEEP,
            filters={"reason": PENDING_RECONCILE_REASON},
        )
    except Exception:
        logger.exception("reconcile_pending_artifacts: failed to query PENDING_RECONCILE records")
        return 0

    reconciled = 0
    for record in pending:
        artifact_id = record.get("_key") or record.get("id")
        document_id = record.get("externalRecordId")
        org_id = record.get("orgId")
        if not artifact_id or not document_id or not org_id:
            logger.warning(
                "reconcile_pending_artifacts: skipping malformed PENDING_RECONCILE record: %s",
                artifact_id or "<unknown>",
            )
            continue
        try:
            artifact_doc = await graph_provider.get_document(artifact_id, CollectionNames.ARTIFACTS.value)
            if not artifact_doc:
                logger.warning(
                    "reconcile_pending_artifacts: no artifacts doc for %s, clearing stale marker", artifact_id,
                )
                await graph_provider.update_node(
                    artifact_id, CollectionNames.RECORDS.value, {"reason": None},
                )
                continue

            version_history = await blob_store.get_document_version_history(org_id, document_id)
            if not version_history:
                continue

            from app.agents.actions.util.blob_staging import fetch_blob_bytes

            content = await fetch_blob_bytes(
                org_id=org_id, config_service=blob_store.config_service, storage_document_id=document_id,
            )
            content_hash = compute_content_hash(content)
            new_version = int(record.get("version", 1)) + 1
            storage_version = version_history[-1].get("version")

            versions = deserialize_artifact_versions(artifact_doc.get("versions"))
            versions.append({
                "registryVersion": new_version,
                "storageVersion": storage_version,
                "contentHash": content_hash,
                "sizeBytes": len(content),
                "createdAt": record.get("updatedAtTimestamp"),
            })

            await graph_provider.update_node(
                artifact_id, CollectionNames.ARTIFACTS.value,
                {"contentHash": content_hash, "sizeInBytes": len(content),
                 "versions": serialize_artifact_versions(versions)},
            )
            await graph_provider.update_node(
                artifact_id, CollectionNames.RECORDS.value,
                {"version": new_version, "sizeInBytes": len(content), "isLatestVersion": True, "reason": None},
            )
            reconciled += 1
            logger.info(
                "reconcile_pending_artifacts: repaired artifact=%s to version=%d", artifact_id, new_version,
            )
        except Exception:
            logger.exception("reconcile_pending_artifacts: failed to reconcile artifact=%s", artifact_id)
    return reconciled


def _get_ttl_seconds() -> int:
    try:
        hours = float(os.environ.get("ARTIFACT_TEMP_TTL_HOURS", str(DEFAULT_TTL_HOURS)))
    except (ValueError, TypeError):
        logger.warning("Invalid ARTIFACT_TEMP_TTL_HOURS, using default %s", DEFAULT_TTL_HOURS)
        hours = float(DEFAULT_TTL_HOURS)
    return int(hours * 3600)


def _get_interval_seconds() -> int:
    try:
        minutes = float(os.environ.get("ARTIFACT_CLEANUP_INTERVAL_MINUTES", str(DEFAULT_INTERVAL_MINUTES)))
    except (ValueError, TypeError):
        logger.warning("Invalid ARTIFACT_CLEANUP_INTERVAL_MINUTES, using default %s", DEFAULT_INTERVAL_MINUTES)
        minutes = float(DEFAULT_INTERVAL_MINUTES)
    return int(minutes * 60)


def _cleanup_temp_directories(sandbox_root: str, ttl_seconds: int) -> int:
    """Remove execution directories older than *ttl_seconds*.

    Returns the number of directories removed.
    """
    if not os.path.isdir(sandbox_root):
        return 0

    now = time.time()
    removed = 0

    for entry in os.scandir(sandbox_root):
        if not entry.is_dir():
            continue
        try:
            age = now - entry.stat().st_mtime
            if age > ttl_seconds:
                shutil.rmtree(entry.path, ignore_errors=True)
                removed += 1
        except OSError:
            pass

    return removed


async def _cleanup_loop(graph_provider: Any = None, blob_store: Any = None) -> None:
    """Background loop that periodically cleans up sandbox temp dirs and,
    when `graph_provider`/`blob_store` are supplied, reconciles artifacts
    stuck in `PENDING_RECONCILE`."""
    interval = _get_interval_seconds()
    ttl = _get_ttl_seconds()

    logger.info(
        "Artifact cleanup started: interval=%ds, ttl=%ds, reconcile=%s",
        interval, ttl, graph_provider is not None and blob_store is not None,
    )

    while True:
        try:
            await asyncio.sleep(interval)
            total_removed = 0

            # Clean both local and docker sandbox roots
            from app.sandbox.local_executor import _SANDBOX_ROOT as local_root
            total_removed += _cleanup_temp_directories(local_root, ttl)

            try:
                from app.sandbox.docker_executor import _SANDBOX_ROOT as docker_root
                total_removed += _cleanup_temp_directories(docker_root, ttl)
            except ImportError:
                pass

            if total_removed > 0:
                logger.info("Artifact cleanup: removed %d expired directories", total_removed)

            if graph_provider is not None and blob_store is not None:
                reconciled = await reconcile_pending_artifacts(graph_provider, blob_store)
                if reconciled > 0:
                    logger.info("Artifact cleanup: reconciled %d pending artifacts", reconciled)

        except asyncio.CancelledError:
            logger.info("Artifact cleanup task cancelled")
            break
        except Exception:
            logger.exception("Error in artifact cleanup loop")


def start_cleanup_task(graph_provider: Any = None, blob_store: Any = None) -> asyncio.Task:
    """Start the background cleanup task. Idempotent -- returns existing task if running.

    `graph_provider`/`blob_store` are optional so existing no-arg call sites
    keep working with temp-directory cleanup only; pass both to also enable
    the `PENDING_RECONCILE` repair pass.
    """
    global _cleanup_task
    if _cleanup_task is not None and not _cleanup_task.done():
        return _cleanup_task

    _cleanup_task = asyncio.create_task(_cleanup_loop(graph_provider, blob_store))
    return _cleanup_task


def stop_cleanup_task() -> None:
    """Cancel the background cleanup task if running."""
    global _cleanup_task
    if _cleanup_task is not None and not _cleanup_task.done():
        _cleanup_task.cancel()
        _cleanup_task = None
