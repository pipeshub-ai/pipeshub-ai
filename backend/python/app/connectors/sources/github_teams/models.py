"""
Domain models shared across all GitHub Teams connector modules.

Plain data containers that cross module boundaries. Keep this file free of
business logic; move any logic that operates on these models into the module
that owns that concern.
"""

from enum import Enum

from pydantic import BaseModel, Field

from app.models.entities import Record
from app.models.permission import Permission


class GitHubLiterals(str, Enum):
    """String constants used as dict keys and sync-point payload fields."""

    LAST_SYNC_TIME = "last_sync_time"
    LAST_COMMIT_SHA = "last_commit_sha"
    DEFAULT_BRANCH = "default_branch"
    FULL_NAME = "full_name"
    REPO_IDS = "repo_ids"
    RECORD_GROUP = "record_group"
    CODE = "code"
    ISSUES = "issues"
    PULLS = "pulls"
    USERS = "users"
    REPO_INVENTORY = "repo-inventory"
    GLOBAL = "global"
    ORG = "org"
    UPDATED_AT = "updated_at"
    UTF_8 = "utf-8"
    IMAGE = "image"
    ATTACHMENT = "attachment"
    EMAIL_RESOLUTION_SWEEP = "email-resolution-sweep"


class RecordUpdate(BaseModel):
    """Carries a Record together with the change flags needed by data_entities_processor.

    All boolean flags are required so every call site states explicitly what
    changed. Mirrors the GitLab connector's contract so the same downstream
    helpers can be reused.
    """

    record: Record
    is_new: bool = Field(description="True when no DB row existed before this sync run")
    is_updated: bool = Field(description="True when an existing row was updated")
    is_deleted: bool = Field(description="True when the source item no longer exists")
    metadata_changed: bool = Field(description="True when title/state/labels changed")
    content_changed: bool = Field(description="True when body/description content changed")
    permissions_changed: bool = Field(description="True when the record's ACL changed")
    old_permissions: list[Permission] | None = Field(default=None, description="Previous permissions (before this sync)")
    new_permissions: list[Permission] | None = Field(default=None, description="Current permissions as of this sync")
    external_record_id: str | None = Field(default=None, description="Connector-scoped external ID of this record")
