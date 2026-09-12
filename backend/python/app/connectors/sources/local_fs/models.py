"""Request/response models for Local FS (API and connector)."""

import math
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator


def _floor_to_epoch_ms(value: object) -> object:
    """Accept the sub-millisecond floats Node's ``fs.Stats`` reports.

    NTFS keeps 100ns ticks, so ``mtimeMs`` arrives as e.g.
    ``1789203332890.1865`` and Pydantic refuses the lossy narrowing to ``int``.
    Floor rather than round so the desktop and the server derive the same
    millisecond from one stat call.
    """
    if isinstance(value, float) and math.isfinite(value):
        return math.floor(value)
    return value


EpochMs = Annotated[int, BeforeValidator(_floor_to_epoch_ms)]


class LocalFsFileEvent(BaseModel):
    type: str
    path: str
    oldPath: str | None = None
    # When the desktop observed the event, which for live and reconcile events
    # is wall-clock rather than a file time. Prefer ``mtimeMs`` for anything
    # that means "when was this file last changed".
    timestamp: EpochMs
    size: int | None = None
    isDirectory: bool
    sha256: str | None = None
    mimeType: str | None = None
    mtimeMs: EpochMs | None = None
    # Inode birth time. Absent on deletions and where the platform/filesystem
    # doesn't report one (e.g. Linux without statx/btime support).
    birthtimeMs: EpochMs | None = None


class LocalFsPullRequest(BaseModel):
    """One page of a server-driven sync run, relayed to the desktop by Node.

    ``orgId``/``userId`` are deliberately absent: Node takes them from the
    scoped token, so a caller cannot address another tenant's desktop.
    """

    connectorId: str
    runId: str
    batchIndex: int
    mode: Literal["FULL", "INCREMENTAL"]
    cursor: str | None = None
    maxEvents: int
    timeoutMs: int


class LocalFsPullBatch(BaseModel):
    """Desktop's answer to a pull.

    ``cursor`` is opaque to the server and denotes the position *after* the
    returned events. ``connectorId`` is echoed so a run can detect a reply
    from a machine other than the one it addressed, and ``deviceId`` names the
    machine that answered so a run can refuse a page from a different one.
    """

    connectorId: str
    runId: str
    batchIndex: int
    deviceId: str | None = None
    cursor: str | None = None
    hasMore: bool
    events: list[LocalFsFileEvent] = []
    rootPath: str | None = None


class LocalFsFileEventBatchStats(BaseModel):
    processed: int
    deleted: int
    skipped: int = 0
