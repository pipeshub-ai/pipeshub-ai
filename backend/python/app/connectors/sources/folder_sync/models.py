"""Request/response models for Folder Sync (API and connector)."""

from pydantic import BaseModel


class FolderSyncFileEvent(BaseModel):
    type: str
    path: str
    oldPath: str | None = None
    timestamp: int
    size: int | None = None
    isDirectory: bool


class FolderSyncFileEventBatchRequest(BaseModel):
    batchId: str
    events: list[FolderSyncFileEvent]
    timestamp: int


class FolderSyncFileEventBatchStats(BaseModel):
    processed: int
    deleted: int
