"""Protocol-neutral directory entries for SMB and CIFS crawls."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime


@dataclass(frozen=True)
class DirectoryEntry:
    name: str
    is_directory: bool
    is_symlink: bool
    size: int
    created_time: datetime | None
    last_write_time: datetime | None
    file_id: int | None


@dataclass(frozen=True)
class ShareInfo:
    name: str
    share_type: str
