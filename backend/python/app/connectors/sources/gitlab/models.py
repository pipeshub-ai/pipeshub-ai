"""
Domain models shared across all GitLab connector modules.

These are plain data containers that cross module boundaries.  Keep this file
free of business logic; move any logic that operates on these models into the
module that owns that concern.
"""

from enum import Enum

from pydantic import BaseModel, Field

from app.models.entities import Record
from app.models.permission import Permission


class GitlabLiterals(str, Enum):
    """String constants used as dict keys and enum values throughout the connector."""

    LAST_SYNC_TIME = "last_sync_time"
    LAST_COMMIT_SHA = "last_commit_sha"
    RECORD_GROUP = "record_group"
    GLOBAL = "global"
    UPDATED_AT = "updated_at"
    UTF_8 = "utf-8"
    IMAGE = "image"
    ATTACHMENT = "attachment"


class FileAttachment(BaseModel):
    """A single attachment (image or file) extracted from a GitLab Markdown body."""

    href: str = Field(description="Relative /uploads/... path from the GitLab instance", min_length=1)
    filename: str = Field(description="Original filename as it appears in the upload URL", min_length=1)
    filetype: str = Field(description="Lowercase file extension (e.g. 'pdf', 'png')")
    category: str = Field(description="'image' or 'attachment'")


class RecordUpdate(BaseModel):
    """Carries a Record together with the change flags needed by data_entities_processor.

    All boolean flags default to False to make construction at call sites
    explicit about what actually changed.  The fields are preserved verbatim
    because they are part of the on_new_records contract; do not prune them
    without verifying the downstream processor.
    """

    record: Record
    is_new: bool = Field(description="True when no DB row existed before this sync run")
    is_updated: bool = Field(description="True when an existing row was updated")
    is_deleted: bool = Field(description="True when the source item no longer exists")
    metadata_changed: bool = Field(description="True when title/state/labels changed")
    content_changed: bool = Field(description="True when body/description content changed")
    permissions_changed: bool = Field(description="True when the record's ACL changed")
    old_permissions: list[Permission] | None = Field(description="Previous permissions (before this sync)")
    new_permissions: list[Permission] | None = Field(description="Current permissions as of this sync")
    external_record_id: str | None = Field(description="Connector-scoped external ID of this record")
