"""Request/response models for Local FS (API and connector)."""

from typing import Literal

from pydantic import BaseModel


class LocalFsFileEvent(BaseModel):
    type: str
    path: str
    oldPath: str | None = None
    timestamp: int
    size: int | None = None
    isDirectory: bool
    sha256: str | None = None
    mimeType: str | None = None


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
