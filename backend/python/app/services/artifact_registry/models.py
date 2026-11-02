"""Pydantic wire/domain models for the artifact registry.

Kept dependency-free of ArangoDB/Neo4j/Mongo shapes on purpose — `registry.py`
and its collaborators translate to/from those at the edges (`IGraphDBProvider`
dicts, `BlobStorage` response dicts), so this module can be unit tested with
zero I/O and stays the stable contract callers (agent tools, hooks) code
against.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from app.models.entities import ArtifactType, ArtifactVisibility, LifecycleStatus

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = [
    "Actor",
    "artifact_owner",
    "artifact_owner_id",
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


def artifact_owner_id(state: Mapping[str, Any]) -> str | None:
    """The user an agent run creates and reads its artifacts as.

    A service-account agent runs as its creator, but what it makes in a chat
    belongs to the person chatting, as the conversation does: the agent route
    sets ``artifact_owner_user_id`` for that case. Otherwise it is the run's
    ``user_id``, read exactly as before (None when the state has none).
    """
    return state.get("artifact_owner_user_id") or state.get("user_id")


def artifact_owner(state: Mapping[str, Any]) -> Actor:
    """`artifact_owner_id` as the `Actor` the artifact registry authorizes."""
    return Actor(org_id=state.get("org_id", ""), user_id=artifact_owner_id(state) or "")


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


class ArtifactCopy(BaseModel):
    """The independent copy `ArtifactRegistryService.copy_to_conversation`
    made, plus how the source's version numbers line up with it.

    `versions` maps a source version to its version in the copy. The two can
    drift when the source has identical content in two versions (the copy
    dedups it) or when an old version's bytes are gone."""

    source_artifact_id: str
    source_document_id: str | None = None
    artifact_id: str
    document_id: str | None = None
    versions: dict[int, int] = Field(default_factory=dict)


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
    visibility: ArtifactVisibility = ArtifactVisibility.VISIBLE
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
            "visibility": self.visibility.value,
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
