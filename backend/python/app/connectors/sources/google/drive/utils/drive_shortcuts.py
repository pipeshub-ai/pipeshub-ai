"""Replace Drive shortcuts with the files they point at.

A shortcut is not a document. Indexing it stores an empty pointer. File
shortcuts are replaced by their target. Folder shortcuts are replaced by the
target folder and the files inside it. Cycles and a depth limit stop a chain
of shortcuts from walking forever.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from app.config.constants.arangodb import MimeTypes
from app.connectors.sources.google.drive.utils.drive_mime import (
    SHORTCUT_MIME,
    DriveNativeKind,
    classify_drive_mime,
)

if TYPE_CHECKING:
    from logging import Logger

MAX_SHORTCUT_DEPTH = 8

GetMetadata = Callable[[str], Awaitable[dict | None]]
ListChildren = Callable[[str, set[str]], Awaitable[list[dict]]]


def shortcut_target(metadata: dict) -> tuple[str | None, str | None]:
    details = metadata.get("shortcutDetails") or {}
    target_id = details.get("targetId")
    target_mime = details.get("targetMimeType")
    if target_id:
        return str(target_id), (str(target_mime) if target_mime else None)
    return None, None


async def materialize_drive_shortcuts(
    items: list[dict],
    *,
    get_metadata: GetMetadata,
    list_children: ListChildren,
    seen: set[str],
    cache: dict[str, dict],
    logger: Logger,
    max_depth: int = MAX_SHORTCUT_DEPTH,
) -> list[dict]:
    """Return concrete files and folders, with shortcuts removed.

    ``seen`` holds ids already emitted or already skipped, including shortcut
    ids themselves so a shortcut is not stored and is not expanded twice.
    ``cache`` remembers targets fetched during this run.
    """
    output: list[dict] = []

    async def walk(batch: list[dict], depth: int, stack: set[str]) -> None:
        for metadata in batch:
            file_id = metadata.get("id")
            if not file_id or file_id in seen:
                continue
            if classify_drive_mime(metadata.get("mimeType")) is not DriveNativeKind.SHORTCUT:
                seen.add(file_id)
                output.append(metadata)
                continue

            target_id, target_mime = shortcut_target(metadata)
            seen.add(file_id)
            if not target_id or target_id in stack or depth >= max_depth:
                logger.warning(
                    "Skipping Drive shortcut %s: target missing, already in this chain, or too deep",
                    file_id,
                )
                continue
            if target_id in seen:
                continue

            target = await _load_target(target_id, get_metadata, cache, logger, file_id)
            if not target:
                continue

            stack.add(target_id)
            is_folder = (
                target.get("mimeType") == MimeTypes.GOOGLE_DRIVE_FOLDER.value
                or target_mime == MimeTypes.GOOGLE_DRIVE_FOLDER.value
            )
            if is_folder and classify_drive_mime(target.get("mimeType")) is not DriveNativeKind.SHORTCUT:
                if target_id not in seen:
                    seen.add(target_id)
                    output.append(target)
                children = await list_children(target_id, seen)
                await walk(children, depth + 1, stack)
            else:
                await walk([target], depth + 1, stack)
            stack.discard(target_id)

    await walk(items, 0, set())
    return output


async def _load_target(
    target_id: str,
    get_metadata: GetMetadata,
    cache: dict[str, dict],
    logger: Logger,
    shortcut_id: str,
) -> dict | None:
    cached = cache.get(target_id)
    if cached is not None:
        return cached
    try:
        target = await get_metadata(target_id)
    except Exception as error:
        logger.warning(
            "Drive shortcut %s target %s could not be read: %s",
            shortcut_id,
            target_id,
            type(error).__name__,
        )
        return None
    if not target:
        logger.warning(
            "Drive shortcut %s target %s is not accessible", shortcut_id, target_id
        )
        return None
    cache[target_id] = target
    return target


def is_shortcut_mime(mime_type: str | None) -> bool:
    return mime_type == SHORTCUT_MIME
