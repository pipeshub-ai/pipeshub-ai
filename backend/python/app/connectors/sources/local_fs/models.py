"""Request/response models for Local FS (API and connector)."""

from pydantic import BaseModel


class LocalFsFileEvent(BaseModel):
    type: str
    path: str
    oldPath: str | None = None
    timestamp: int
    size: int | None = None
    isDirectory: bool


class LocalFsFileEventBatchRequest(BaseModel):
    batchId: str
    events: list[LocalFsFileEvent]
    timestamp: int


class LocalFsFileEventBatchStats(BaseModel):
    processed: int
    deleted: int
