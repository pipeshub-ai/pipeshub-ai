from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any


class SyncStatus(Enum):
    """Enumeration of sync statuses"""

    NOT_STARTED = "NOT_STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    PAUSED = "PAUSED"
    PARTIAL = "PARTIAL"


@dataclass
class SyncProgress:
    """Progress tracking for sync operations"""

    total_items: int
    processed_items: int
    failed_items: int
    status: SyncStatus
    start_time: datetime
    last_update: datetime
    percentage: float = 0.0


class ISyncService(ABC):
    """Base interface for synchronization operations for a data source"""

    @abstractmethod
    async def initialize(
        self, org_id: str, sync_config: dict[str, Any] | None = None
    ) -> bool:
        """Initialize the sync service"""

    @abstractmethod
    async def connect_services(self, org_id: str) -> bool:
        """Connect to the services"""

    @abstractmethod
    async def init_sync(self, org_id: str) -> bool:
        """Initial sync"""

    @abstractmethod
    async def start_sync(
        self, org_id: str, sync_config: dict[str, Any] | None = None
    ) -> bool:
        """Start a sync operation"""

    @abstractmethod
    async def pause_sync(self, org_id: str, sync_id: str | None = None) -> bool:
        """Pause a sync operation"""

    @abstractmethod
    async def resume_sync(self, org_id: str, sync_id: str | None = None) -> bool:
        """Resume a sync operation"""

    @abstractmethod
    async def stop_sync(self, org_id: str, sync_id: str | None = None) -> bool:
        """Stop a sync operation"""

    @abstractmethod
    async def get_sync_progress(
        self, org_id: str, sync_id: str | None = None
    ) -> SyncProgress:
        """Get sync progress"""

    @abstractmethod
    async def get_sync_history(
        self, org_id: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Get sync history"""
