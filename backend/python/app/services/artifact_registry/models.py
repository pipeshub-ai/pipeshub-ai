"""Pydantic wire/domain models for the artifact registry.

Kept dependency-free of ArangoDB/Neo4j/Mongo shapes on purpose — `registry.py`
and its collaborators translate to/from those at the edges (`IGraphDBProvider`
dicts, `BlobStorage` response dicts), so this module can be unit tested with
zero I/O and stays the stable contract callers (agent tools, hooks) code
against.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.models.entities import ArtifactType, LifecycleStatus

__all__ = [
    "Actor",
    "ArtifactMetadata",
    "ArtifactVersion",
    "ArtifactLineage",
    "UploadGrant",
]


class Actor(BaseModel):
    """Who is asking. Bundled once so every registry method takes exactly
    one identity parameter instead of a growing `(org_id, user_id, ...)`
    tuple, and so `AccessPolicy` has one stable type to authorize against.

    Model-supplied artifact IDs are the untrusted input here — `Actor` is
    always constructed by the harness/tool layer from the authenticated
    request context, never from LLM output.
    """

    org_id: str
    user_id: str


class ArtifactVersion(BaseModel):
    """One immutable version of an artifact's content."""

    version: int
    size_bytes: int
    content_hash: str
    mime_type: str
    created_at: int
    created_by_user_id: str | None = None
    # True when `VersionManager.add_version` detected identical content to
    # the previous version (content-hash dedup) and returned the EXISTING
    # version instead of creating a new one.
    deduplicated: bool = False


class ArtifactLineage(BaseModel):
    """One `DERIVED_FROM` edge, auto-captured by the harness — never
    model-asserted. See `lineage.py`."""

    output_artifact_id: str
    code_artifact_id: str
    code_version: int
    output_version: int


class ArtifactMetadata(BaseModel):
    """The registry's canonical view of one artifact, current version plus
    identity — what `save_artifact`/`list_artifacts`/the sandbox bridge all
    exchange."""

    artifact_id: str
    org_id: str
    conversation_id: str | None
    name: str
    logical_name: str
    artifact_type: ArtifactType
    mime_type: str
    description: str = ""
    lifecycle_status: LifecycleStatus = LifecycleStatus.PUBLISHED
    version: int
    size_bytes: int
    content_hash: str | None = None
    source_tool: str | None = None
    document_id: str | None = None
    result_schema: dict[str, Any] | None = None
    is_temporary: bool = False
    created_at: int | None = None
    updated_at: int | None = None
    # Populated by `list_for_conversation(include_lineage=True)` /
    # `resolve()` — the code artifact this output was derived from, if any.
    derived_from_code_artifact_id: str | None = None
    derived_from_code_version: int | None = None

    def to_tool_response(self) -> dict:
        """Compact block appended to a tool's response so the model sees
        IDs/names/versions immediately (see `sandbox_bridge.py`'s
        synchronous-registration change) — never the full metadata object,
        to keep the on-the-wire shape small and stable."""
        block = {
            "artifact_id": self.artifact_id,
            "name": self.name,
            "version": self.version,
            "mime_type": self.mime_type,
            "size_bytes": self.size_bytes,
            "artifact_type": self.artifact_type.value,
        }
        if self.derived_from_code_artifact_id:
            block["derived_from_code_artifact_id"] = self.derived_from_code_artifact_id
        return block


class UploadGrant(BaseModel):
    """A short-lived, server-verified permission to PUT the next version of
    a specific artifact directly to blob storage — the two-phase flow that
    closes the "presigned PUT can't cap size" hole (see
    `signed_urls.py::SignedUrlBroker`).

    Never trust the client's declared size/hash for anything except
    generating the grant — `commit_version` re-verifies both against the
    actually-stored object before bumping the version.
    """

    grant_id: str
    artifact_id: str
    upload_url: str
    document_id: str
    declared_size: int
    declared_sha256: str
    expires_at: int
