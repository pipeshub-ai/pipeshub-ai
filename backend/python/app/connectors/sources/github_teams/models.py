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


def blob_external_id(repo_id: int, repo_path: str) -> str:
    """Stable, rename-proof identity for a code file: anchored on repo.id, not owner/repo."""
    return f"/{repo_id}/blob/{repo_path}"


def tree_external_id(repo_id: int, repo_path: str) -> str:
    """Stable, rename-proof identity for a folder: anchored on repo.id, not owner/repo."""
    return f"/{repo_id}/tree/{repo_path}"


def path_from_external_id(repo_id: int, external_id: str) -> str | None:
    """Invert ``blob_external_id``/``tree_external_id`` back to the repo path.

    Folders need this: ``FileRecord`` has no ``file_path`` attribute, so their
    path exists only inside the external id. Returns ``None`` for ids that
    match neither shape (foreign records can never be mistaken for a path).
    """
    for kind in ("blob", "tree"):
        prefix = f"/{repo_id}/{kind}/"
        if external_id.startswith(prefix):
            return external_id[len(prefix):]
    return None


class GitHubLiterals(str, Enum):
    """String constants used as dict keys and sync-point payload fields."""

    LAST_SYNC_TIME = "last_sync_time"
    LAST_COMMIT_SHA = "last_commit_sha"
    DEFAULT_BRANCH = "default_branch"
    FULL_NAME = "full_name"
    UTF_8 = "utf-8"
    IMAGE = "image"


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
