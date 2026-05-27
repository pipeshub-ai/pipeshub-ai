"""Request/response models for Local FS (API and connector)."""

from pydantic import BaseModel


class LocalFsFileEvent(BaseModel):
    type: str
    path: str
    oldPath: str | None = None
    timestamp: int
    size: int | None = None
    isDirectory: bool
    contentField: str | None = None
    sha256: str | None = None
    mimeType: str | None = None


class LocalFsFileEventBatchRequest(BaseModel):
    batchId: str
    events: list[LocalFsFileEvent]
    timestamp: int
    resetBeforeApply: bool = False


class LocalFsFileEventBatchStats(BaseModel):
    processed: int
    deleted: int


class LocalFsFileEventSubmissionResponse(BaseModel):
    """Response contract for Local FS file-event submission APIs."""

    success: bool
    connectorId: str
    batchId: str
    stats: LocalFsFileEventBatchStats
