"""`ArtifactRegistryService` — the single façade every caller (agent tools,
the sandbox bridge's PRE/POST hooks, history seeding, sub-agent
propagation) goes through. Owns composition of `AccessPolicy`/
`VersionManager`/`LineageTracker`/`SignedUrlBroker`; callers never touch
those collaborators directly (Single Responsibility + Dependency
Inversion — see plan's "Design Principles Applied").

Every method takes an `Actor` and authorizes before acting — model-supplied
artifact IDs/names are always untrusted input (see `models.Actor`).
"""

from __future__ import annotations

import logging
import os
from typing import Any

from app.config.constants.arangodb import CollectionNames, Connectors
from app.models.entities import ArtifactType

from .access import AccessPolicy
from .lineage import LineageTracker
from .models import Actor, ArtifactMetadata, ArtifactVersion, UploadGrant
from .signed_urls import GrantVerificationError, SignedUrlBroker
from .versioning import VersionManager, to_metadata_from_docs

logger = logging.getLogger(__name__)

__all__ = ["ArtifactRegistryService", "MAX_ARTIFACT_BYTES"]

# Same cap the legacy sandbox pipeline enforces (`app/sandbox/artifact_upload.py`)
# — kept in sync there via a re-export so there is exactly one number.
MAX_ARTIFACT_BYTES = 25 * 1024 * 1024

# Extensions tried by `resolve()`'s fuzzy fallback when a model passes an
# `input_artifacts`/`run_code` ref without (or with the wrong) extension —
# covers the artifact types this pipeline actually produces (images from
# `generate_image`, documents from `run_code`, data/text from
# `save_artifact`). Deliberately NOT exhaustive: an unbounded guess list
# risks matching the WRONG artifact when several share a stem.
_FUZZY_RESOLVE_EXTENSIONS: tuple[str, ...] = (
    ".png", ".jpg", ".jpeg", ".svg", ".pdf", ".pptx", ".docx",
    ".xlsx", ".csv", ".md", ".txt", ".json",
)


class ArtifactRegistryService:
    def __init__(self, graph_provider: Any, blob_store: Any, *, max_bytes: int = MAX_ARTIFACT_BYTES) -> None:
        self._graph_provider = graph_provider
        self._blob_store = blob_store
        self._access = AccessPolicy(graph_provider)
        self._versions = VersionManager(graph_provider, blob_store, self._access)
        self._lineage = LineageTracker(graph_provider)
        self._urls = SignedUrlBroker(blob_store, max_bytes)
        self._max_bytes = max_bytes

    @property
    def access(self) -> AccessPolicy:
        return self._access

    @property
    def lineage(self) -> LineageTracker:
        return self._lineage

    # ------------------------------------------------------------------
    # Create / version
    # ------------------------------------------------------------------

    async def register(
        self, *, actor: Actor, name: str, artifact_type: ArtifactType, mime_type: str,
        content: bytes, conversation_id: str | None, description: str = "",
        source_tool: str | None = None, is_temporary: bool = False,
        connector_name: Connectors = Connectors.CODING_SANDBOX,
    ) -> ArtifactMetadata:
        """Create a version-1 artifact. Use `register_output` instead when
        the caller wants "match an existing logical name in this
        conversation, else create" semantics (the `run_code` output path)."""
        self._check_size(content)
        return await self._versions.create(
            actor=actor, name=name, artifact_type=artifact_type, mime_type=mime_type,
            content=content, conversation_id=conversation_id, description=description,
            source_tool=source_tool, is_temporary=is_temporary, connector_name=connector_name,
        )

    async def register_output(
        self, *, actor: Actor, name: str, artifact_type: ArtifactType, mime_type: str,
        content: bytes, conversation_id: str, description: str = "",
        source_tool: str | None = None, connector_name: Connectors = Connectors.CODING_SANDBOX,
    ) -> tuple[ArtifactMetadata, ArtifactVersion | None]:
        """The `run_code`/`image_generator`/etc. output path: resolve `name`
        against existing artifacts IN THIS CONVERSATION first — a re-run
        producing `sales_chart.png` again bumps the EXISTING artifact's
        version instead of creating a new, disconnected one (Design
        Decision #2 in the plan). Returns `(metadata, version)`; `version`
        is `None` when a brand-new artifact was created (there is no
        "version bump" event for version 1)."""
        self._check_size(content)
        existing = await self.resolve_by_logical_name(actor, name, conversation_id=conversation_id)
        if existing is None:
            metadata = await self._versions.create(
                actor=actor, name=name, artifact_type=artifact_type, mime_type=mime_type,
                content=content, conversation_id=conversation_id, description=description,
                source_tool=source_tool, connector_name=connector_name,
            )
            return metadata, None

        version, metadata = await self._versions.add_version(
            actor=actor, artifact_id=existing.artifact_id, content=content, mime_type=mime_type,
        )
        return metadata, version

    async def register_existing(
        self, *, actor: Actor, document_id: str, name: str, artifact_type: ArtifactType, mime_type: str,
        size_bytes: int, conversation_id: str | None, description: str = "", source_tool: str | None = None,
        content_hash: str | None = None, connector_name: Connectors = Connectors.CODING_SANDBOX,
    ) -> ArtifactMetadata:
        """See `VersionManager.create_from_existing_document` — for a blob
        already uploaded by the caller (e.g. `database_sandbox.py`'s CSV
        export)."""
        return await self._versions.create_from_existing_document(
            actor=actor, document_id=document_id, name=name, artifact_type=artifact_type,
            mime_type=mime_type, size_bytes=size_bytes, conversation_id=conversation_id,
            description=description, source_tool=source_tool, content_hash=content_hash,
            connector_name=connector_name,
        )

    async def add_version(
        self, *, actor: Actor, artifact_id: str, content: bytes, mime_type: str | None = None,
        expected_version: int | None = None,
    ) -> tuple[ArtifactVersion, ArtifactMetadata]:
        self._check_size(content)
        return await self._versions.add_version(
            actor=actor, artifact_id=artifact_id, content=content, mime_type=mime_type,
            expected_version=expected_version,
        )

    # ------------------------------------------------------------------
    # Two-phase upload (large content the caller can't pass inline)
    # ------------------------------------------------------------------

    async def get_upload_grant(
        self, *, actor: Actor, artifact_id: str, declared_size: int, declared_sha256: str,
        mime_type: str, ttl_s: int = 600,
    ) -> UploadGrant:
        record = await self._access.authorize_write(actor, artifact_id)
        return await self._urls.get_upload_grant(
            org_id=actor.org_id, user_id=actor.user_id, artifact_id=artifact_id,
            document_id=record["externalRecordId"], declared_size=declared_size,
            declared_sha256=declared_sha256, mime_type=mime_type, ttl_s=ttl_s,
        )

    async def commit_version(self, *, actor: Actor, grant_id: str) -> tuple[ArtifactVersion, ArtifactMetadata]:
        """Verify the actually-uploaded object matches what the grant
        declared, THEN bump the version — never trust the client's PUT to
        have matched its own declaration (see `signed_urls.py` module
        docstring)."""
        grant = self._urls.pop_grant(grant_id, org_id=actor.org_id, user_id=actor.user_id)
        from app.agents.actions.util.blob_staging import fetch_blob_bytes

        content = await fetch_blob_bytes(
            org_id=actor.org_id, config_service=self._blob_store.config_service,
            storage_document_id=grant["document_id"],
        )
        from .versioning import compute_content_hash

        actual_hash = compute_content_hash(content)
        if len(content) != grant["declared_size"] or actual_hash != grant["declared_sha256"]:
            raise GrantVerificationError(
                f"Uploaded content for artifact {grant['artifact_id']} does not match the "
                f"declared grant (size/hash mismatch) — refusing to commit this version."
            )
        return await self._versions.add_version(
            actor=actor, artifact_id=grant["artifact_id"], content=content, mime_type=grant["mime_type"],
        )

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def resolve(self, *, actor: Actor, ref: str, conversation_id: str | None = None) -> ArtifactMetadata:
        """Resolve `ref` (an `artifact_id`, or a logical name scoped to
        `conversation_id`) to its current metadata, authorizing as it
        goes. Tries `ref` as an ID first (a UUID artifact_id will never
        collide with a human-chosen logical file name in practice), falls
        back to logical-name lookup, then to an extension-fuzzy variant of
        the logical-name lookup (see `_resolve_by_logical_name_fuzzy`) —
        models routinely drop or guess wrong on the file extension when
        recalling a name from an earlier tool response's prose."""
        try:
            record = await self._access.authorize_read(actor, ref)
        except Exception:
            if conversation_id is None:
                raise
            found = await self.resolve_by_logical_name(actor, ref, conversation_id=conversation_id)
            if found is None:
                found = await self._resolve_by_logical_name_fuzzy(actor, ref, conversation_id=conversation_id)
            if found is None:
                from .access import ArtifactNotFoundError
                raise ArtifactNotFoundError(f"No artifact named {ref!r} in this conversation") from None
            return found
        artifact_doc = await self._graph_provider.get_document(ref, CollectionNames.ARTIFACTS.value)
        if not artifact_doc:
            from .access import ArtifactNotFoundError
            raise ArtifactNotFoundError(f"Artifact metadata missing for: {ref}")
        return await self._with_lineage(to_metadata_from_docs(record, artifact_doc))

    async def resolve_by_logical_name(
        self, actor: Actor, name: str, *, conversation_id: str,
    ) -> ArtifactMetadata | None:
        """Backend-agnostic equality lookup via `get_documents_paginated`
        (no raw AQL/Cypher — works unchanged on ArangoDB and Neo4j)."""
        candidates = await self._graph_provider.get_documents_paginated(
            CollectionNames.ARTIFACTS.value, skip=0, limit=1,
            filters={"orgId": actor.org_id, "conversationId": conversation_id, "logicalName": name},
        )
        if not candidates:
            return None
        artifact_doc = candidates[0]
        artifact_id = artifact_doc.get("_key") or artifact_doc.get("id")
        try:
            record = await self._access.authorize_read(actor, artifact_id)
        except Exception:
            return None
        return await self._with_lineage(to_metadata_from_docs(record, artifact_doc))

    async def _resolve_by_logical_name_fuzzy(
        self, actor: Actor, ref: str, *, conversation_id: str,
    ) -> ArtifactMetadata | None:
        """Extension-tolerant fallback for `resolve()`, tried only after an
        EXACT logical-name match already failed. Two cases, in order:

        1. `ref` has no extension (e.g. `taj_mahal_world_wonder`) — try it
           with each of `_FUZZY_RESOLVE_EXTENSIONS` appended, since the
           artifact is almost always registered WITH one (image/document
           output pipelines always name their output with an extension).
        2. `ref` has an extension but the wrong one (e.g. a model guesses
           `.jpg` for an artifact actually saved as `.png`) — try the bare
           stem in case some OTHER caller registered it without an
           extension at all.

        First match wins — this is a convenience fallback for a model's
        imprecise recall, not a search feature, so it deliberately does
        not rank or return multiple candidates. Returns `None` (never
        raises) so `resolve()`'s existing `ArtifactNotFoundError` message
        stays the one the caller sees when nothing matches."""
        stem, ext = os.path.splitext(ref)
        if not ext:
            candidates = [ref + candidate_ext for candidate_ext in _FUZZY_RESOLVE_EXTENSIONS]
        elif stem:
            candidates = [stem]
        else:
            candidates = []
        for candidate in candidates:
            found = await self.resolve_by_logical_name(actor, candidate, conversation_id=conversation_id)
            if found is not None:
                logger.info(
                    "resolve(): fuzzy-matched ref=%r -> logicalName=%r (artifact_id=%s)",
                    ref, candidate, found.artifact_id,
                )
                return found
        return None

    async def get_content(self, *, actor: Actor, artifact_id: str, version: int | None = None) -> bytes:
        record = await self._access.authorize_read(actor, artifact_id)
        if version is not None and version != record.get("version"):
            raise NotImplementedError(
                "Fetching a specific historical version's content is not yet supported — "
                "only the current version can be retrieved."
            )
        from app.agents.actions.util.blob_staging import fetch_blob_bytes

        return await fetch_blob_bytes(
            org_id=actor.org_id, config_service=self._blob_store.config_service,
            storage_document_id=record["externalRecordId"],
        )

    async def get_download_url(self, *, actor: Actor, artifact_id: str, ttl_s: int = 600) -> str:
        record = await self._access.authorize_read(actor, artifact_id)
        return await self._urls.get_download_url(org_id=actor.org_id, document_id=record["externalRecordId"], ttl_s=ttl_s)

    async def list_for_conversation(
        self, *, actor: Actor, conversation_id: str, include_lineage: bool = True, limit: int = 100,
    ) -> list[ArtifactMetadata]:
        docs = await self._graph_provider.get_documents_paginated(
            CollectionNames.ARTIFACTS.value, skip=0, limit=limit,
            filters={"orgId": actor.org_id, "conversationId": conversation_id},
        )
        results: list[ArtifactMetadata] = []
        for artifact_doc in docs:
            artifact_id = artifact_doc.get("_key") or artifact_doc.get("id")
            try:
                record = await self._access.authorize_read(actor, artifact_id)
            except Exception:
                continue
            metadata = to_metadata_from_docs(record, artifact_doc)
            if include_lineage:
                metadata = await self._with_lineage(metadata)
            results.append(metadata)
        return results

    # ------------------------------------------------------------------
    # Lineage
    # ------------------------------------------------------------------

    async def record_derivation(self, *, output_artifact_id: str, code_artifact_id: str, code_version: int, output_version: int) -> None:
        """Harness-only entry point (never exposed as an LLM tool) — see
        `LineageTracker.record_derivation`."""
        await self._lineage.record_derivation(
            output_artifact_id=output_artifact_id, code_artifact_id=code_artifact_id,
            code_version=code_version, output_version=output_version,
        )

    async def _with_lineage(self, metadata: ArtifactMetadata) -> ArtifactMetadata:
        lineage = await self._lineage.get_lineage_for_output(metadata.artifact_id)
        if lineage is not None:
            metadata.derived_from_code_artifact_id = lineage.code_artifact_id
            metadata.derived_from_code_version = lineage.code_version
        return metadata

    def _check_size(self, content: bytes) -> None:
        if len(content) > self._max_bytes:
            raise ValueError(
                f"Artifact content ({len(content)} bytes) exceeds the {self._max_bytes}-byte cap"
            )
