"""Periodic cleanup of temporary sandbox artifacts.

Runs as a background asyncio task, cleaning up:
1. Local temp directories older than the configured TTL.
2. (Future) ArtifactRecord entries marked as temporary and expired.

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

logger = logging.getLogger(__name__)

DEFAULT_TTL_HOURS = 1
DEFAULT_INTERVAL_MINUTES = 30

_cleanup_task: asyncio.Task | None = None


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


async def _cleanup_loop() -> None:
    """Background loop that periodically cleans up sandbox temp dirs."""
    interval = _get_interval_seconds()
    ttl = _get_ttl_seconds()

    logger.info(
        "Artifact cleanup started: interval=%ds, ttl=%ds",
        interval, ttl,
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

        except asyncio.CancelledError:
            logger.info("Artifact cleanup task cancelled")
            break
        except Exception:
            logger.exception("Error in artifact cleanup loop")


def start_cleanup_task() -> asyncio.Task:
    """Start the background cleanup task. Idempotent -- returns existing task if running."""
    global _cleanup_task
    if _cleanup_task is not None and not _cleanup_task.done():
        return _cleanup_task

    _cleanup_task = asyncio.create_task(_cleanup_loop())
    return _cleanup_task


def stop_cleanup_task() -> None:
    """Cancel the background cleanup task if running."""
    global _cleanup_task
    if _cleanup_task is not None and not _cleanup_task.done():
        _cleanup_task.cancel()
        _cleanup_task = None
