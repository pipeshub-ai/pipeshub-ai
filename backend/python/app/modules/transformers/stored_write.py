"""Vocabulary of a stored-record write: its layout, its run, and the mapping's switch fields.

A stored write puts a record's content into a storage document and names that
document on the virtual record's mapping node. Two layouts exist:

``cow``
    Shadow or read is on. The write goes to a **new** document; the mapping then
    switches to it by compare-and-set on ``{documentId, switchSeq}``, together with the
    write's run token, and the replaced document is marked retired.
``in_place``
    Both are off (the default). The write goes into the mapped document, but the
    mapping is touched only by compare-and-set, invalidated first (a ``none`` run is
    never served), so a set built for an earlier copy-on-write run is never served
    over the new content.

The layout, the generation and the run come from **one** read of the platform
settings (``artifact_settings``); nothing below the sink reads them again.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import TYPE_CHECKING, Any, NamedTuple

from app.modules.pipeline.leases import RecordBusy

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from app.services.featureflag.platform_settings import ArtifactSettings

logger = logging.getLogger(__name__)

#: ``TransformContext.settings`` key: the ``StoredWrite`` this index run writes under.
STORED_WRITE = "stored_write"
#: ``TransformContext.settings`` key: set when the stored write lost its compare-and-set.
#: The record's content is then not what this run stored: no artifact set, no flip.
RUN_SUPERSEDED = "run_superseded"

COW = "cow"
IN_PLACE = "in_place"

#: Retired entries one mapping keeps. A switch past it drops the oldest entry, whose
#: document then falls to the stray pass.
MAX_RETIRED = 16
#: A writer that took longer than this between its create and its switch does not switch:
#: its document may be old enough for the stray pass.
WRITER_DEADLINE_SECONDS = 30 * 60
#: Attempts of one in-place buffer PUT (two retries) and the pauses before them.
IN_PLACE_PUT_ATTEMPTS = 3
IN_PLACE_PUT_BACKOFF_SECONDS = (0.5, 1.0)


class StoredWrite(NamedTuple):
    """The layout, generation and run of one stored write, minted from one settings read."""

    layout: str
    generation: str | None
    run: str


def new_run(generation: str | None) -> str:
    return f"{generation or 'none'}:{uuid.uuid4().hex}"


def mint_stored_write(settings: ArtifactSettings) -> StoredWrite:
    """The write for this settings read. An in-place write always carries a ``none`` run."""
    if settings.layout != COW:
        return StoredWrite(IN_PLACE, None, new_run(None))
    return StoredWrite(COW, settings.generation, new_run(settings.generation))


def in_place_write() -> StoredWrite:
    """For a caller that has no settings of its own: the layout that can never be served."""
    return StoredWrite(IN_PLACE, None, new_run(None))


def is_none_run(run: object) -> bool:
    return isinstance(run, str) and run.startswith("none:")


def run_generation(run: object) -> str | None:
    if not isinstance(run, str) or ":" not in run:
        return None
    return run.split(":", 1)[0]


def classification_sidecar_token_path(stage_version: int) -> str:
    """A classification sidecar's path, named by the write that makes it: the token is the path.

    Two writes in one revision never share a file, and a sidecar nothing names is invisible to a
    reader, so a write that loses its compare-and-set leaves nothing a reader could serve.
    """
    return f"enrich/classification.{uuid.uuid4().hex}@{stage_version}.json"


class SidecarWrite(NamedTuple):
    """How a copy-on-write metadata write files the classification a reader serves beside the set.

    ``write(path, stored_run, semantic_metadata)`` stores the envelope at *path* in revision
    *rev*'s set, never raising: a missing sidecar only makes the read ``stale_sidecar``.
    """

    stage_version: int
    write: Callable[[str, str | None, dict[str, Any]], Awaitable[object]]


class RunSuperseded(Exception):
    """This write lost its compare-and-set (another write switched the mapping, the
    record is gone, or the writer's deadline passed). Its document, if any, is a stray."""


class StoredWriteBusy(RecordBusy):
    """The mapping kept moving under an in-place write's invalidation. The record is retried."""


_BOOTTIME = {"warned": False}


def boottime() -> float:
    """Seconds on a clock that keeps counting while the machine is suspended."""
    clock = getattr(time, "CLOCK_BOOTTIME", None)
    if clock is None:
        if not _BOOTTIME["warned"]:
            _BOOTTIME["warned"] = True
            logger.warning("CLOCK_BOOTTIME is not available; the writer deadline uses the monotonic clock")
        return time.monotonic()
    return time.clock_gettime(clock)


def retired_list(node: dict[str, Any] | None) -> list[dict[str, Any]]:
    value = (node or {}).get("retired")
    return [e for e in value if isinstance(e, dict)] if isinstance(value, list) else []


def with_retired(
    node: dict[str, Any] | None, replaced: str | None, new_document: str, now_ms: int,
) -> tuple[list[dict[str, Any]], int]:
    """The node's ``retired`` list with the replaced document added, and how many entries
    were dropped to stay within ``MAX_RETIRED``. Nothing is added for a first write or
    when the writer's own document is the one replaced."""
    entries = retired_list(node)
    if replaced and replaced != new_document:
        entries.append({
            "doc": replaced,
            "rev": (node or {}).get("storedRev"),
            "sidecar": (node or {}).get("storedMeta"),
            "at": now_ms,
        })
    overflow = max(0, len(entries) - MAX_RETIRED)
    return entries[overflow:], overflow
