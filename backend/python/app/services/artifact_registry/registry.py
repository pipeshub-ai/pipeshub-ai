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
from app.models.entities import ArtifactType, deserialize_artifact_versions

from .access import AccessPolicy
from .lineage import LineageTracker
from .models import Actor, ArtifactMetadata, ArtifactVersion, UploadGrant
from .signed_urls import GrantVerificationError, SignedUrlBroker
from .versioning import (
    VersionConflictError,
    VersionManager,
    VersionMappingNotFoundError,
    resolve_storage_version,
    to_metadata_from_docs,
)

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

# Bounded retry for the "bump-bump" version race in `_bump_with_retry`: a
# concurrent writer's version bump between our `resolve` and our
# `add_version` call fails once with `VersionConflictError`; we re-resolve
# and retry exactly once before surfacing the error to the caller. Content-
# hash dedup in `add_version` makes the retry cheap when the racer wrote
# byte-identical content.
_MAX_VERSION_CONFLICT_RETRIES = 1


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
        result_schema: dict | None = None,
    ) -> ArtifactMetadata:
        """Create a version-1 artifact. Use `register_output` instead when
        the caller wants "match an existing logical name in this
        conversation, else create" semantics (the `run_code` output path)."""
        self._check_size(content)
        return await self._versions.create(
            actor=actor, name=name, artifact_type=artifact_type, mime_type=mime_type,
            content=content, conversation_id=conversation_id, description=description,
            source_tool=source_tool, is_temporary=is_temporary, connector_name=connector_name,
            result_schema=result_schema,
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
            created = await self._versions.create(
                actor=actor, name=name, artifact_type=artifact_type, mime_type=mime_type,
                content=content, conversation_id=conversation_id, description=description,
                source_tool=source_tool, connector_name=connector_name,
            )
            return await self._reconcile_create_race(
                actor=actor, name=name, conversation_id=conversation_id,
                created=created, content=content, mime_type=mime_type,
            )

        return await self._bump_with_retry(
            actor=actor, artifact_id=existing.artifact_id, content=content, mime_type=mime_type,
            expected_version=existing.version,
        )

    async def _bump_with_retry(
        self, *, actor: Actor, artifact_id: str, content: bytes, mime_type: str | None,
        expected_version: int,
    ) -> tuple[ArtifactMetadata, ArtifactVersion | None]:
        """Bump `artifact_id`, pinning `expected_version` so a concurrent
        writer that already moved the version out from under us raises
        `VersionConflictError` instead of silently double-bumping (the
        "bump-bump" race — see plan's "Version races"). Re-resolves and
        retries once, bounded by `_MAX_VERSION_CONFLICT_RETRIES`, before
        surfacing the conflict to the caller."""
        for attempt in range(_MAX_VERSION_CONFLICT_RETRIES + 1):
            try:
                version, metadata = await self._versions.add_version(
                    actor=actor, artifact_id=artifact_id, content=content, mime_type=mime_type,
                    expected_version=expected_version,
                )
                return metadata, version
            except VersionConflictError:
                if attempt >= _MAX_VERSION_CONFLICT_RETRIES:
                    raise
                logger.warning(
                    "register_output: version conflict on artifact %s (expected=%d) — "
                    "re-resolving and retrying once.",
                    artifact_id, expected_version,
                )
                refreshed = await self.resolve(actor=actor, ref=artifact_id)
                expected_version = refreshed.version
        raise AssertionError("unreachable: loop above always returns or raises")

    async def _reconcile_create_race(
        self, *, actor: Actor, name: str, conversation_id: str, created: ArtifactMetadata,
        content: bytes, mime_type: str,
    ) -> tuple[ArtifactMetadata, ArtifactVersion | None]:
        """Guards the "create-create" race: two concurrent `register_output`
        calls for a NEW logical name can both see `existing is None` and
        both `create()`, leaving two artifacts with the same `logicalName`
        in one conversation. `resolve_by_logical_name`'s `limit=1` lookup
        then arbitrarily and permanently shadows one of them.

        Re-resolving right after our own `create()` detects this: if a
        DIFFERENT artifact_id now wins the name lookup, we lost the race —
        fold our content into the winner as a new version instead of
        leaving our own `create()` as a silently orphaned duplicate, and
        mark our record deleted so it stops showing up via direct
        artifact_id access too. No portable unique constraint exists across
        ArangoDB/Neo4j, so this is a best-effort post-hoc fix-up, not a
        prevention — the window between `create()` and this re-resolve is
        real, if narrow."""
        winner = await self.resolve_by_logical_name(actor, name, conversation_id=conversation_id)
        if winner is None or winner.artifact_id == created.artifact_id:
            return created, None

        logger.warning(
            "register_output: create-create race on logicalName=%r conversation=%s — "
            "artifact %s lost to %s; folding content into the winner and marking "
            "the loser deleted.",
            name, conversation_id, created.artifact_id, winner.artifact_id,
        )
        try:
            metadata, version = await self._bump_with_retry(
                actor=actor, artifact_id=winner.artifact_id, content=content, mime_type=mime_type,
                expected_version=winner.version,
            )
        except Exception:
            logger.critical(
                "register_output: failed to fold artifact %s into race winner %s — "
                "both now exist under logicalName=%r; manual cleanup may be needed.",
                created.artifact_id, winner.artifact_id, name, exc_info=True,
            )
            return created, None

        try:
            await self._graph_provider.update_node(
                created.artifact_id, CollectionNames.RECORDS.value,
                {"isDeleted": True, "reason": "ARTIFACT_CREATE_RACE_LOSER"},
            )
        except Exception:
            logger.critical(
                "register_output: folded artifact %s into %s but failed to mark the "
                "loser record deleted — it remains visible via direct artifact_id lookup.",
                created.artifact_id, winner.artifact_id, exc_info=True,
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
        (no raw AQL/Cypher — works unchanged on ArangoDB and Neo4j).

        Fetches a small window (not just 1) and skips any candidate whose
        `records` doc is `isDeleted` — the `ARTIFACTS` collection itself
        carries no `isDeleted`/soft-delete flag (see
        `ArtifactRecord.to_arango_artifact_record`), so a `logicalName`
        that briefly had two documents from the "create-create" race (see
        `_reconcile_create_race`) would otherwise nondeterministically
        resolve to the deleted loser instead of the live winner. This is a
        bounded mitigation, not a uniqueness guarantee: an unbounded number
        of racers could still exceed the window."""
        candidates = await self._graph_provider.get_documents_paginated(
            CollectionNames.ARTIFACTS.value, skip=0, limit=5,
            filters={"orgId": actor.org_id, "conversationId": conversation_id, "logicalName": name},
        )
        for artifact_doc in candidates:
            artifact_id = artifact_doc.get("_key") or artifact_doc.get("id")
            try:
                record = await self._access.authorize_read(actor, artifact_id)
            except Exception:
                continue
            if record.get("isDeleted"):
                continue
            return await self._with_lineage(to_metadata_from_docs(record, artifact_doc))
        return None

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
        storage_version = await self._resolve_storage_version(artifact_id, record, version)
        from app.agents.actions.util.blob_staging import fetch_blob_bytes

        return await fetch_blob_bytes(
            org_id=actor.org_id, config_service=self._blob_store.config_service,
            storage_document_id=record["externalRecordId"], version=storage_version,
        )

    async def get_download_url(
        self, *, actor: Actor, artifact_id: str, version: int | None = None, ttl_s: int = 600,
    ) -> str:
        record = await self._access.authorize_read(actor, artifact_id)
        storage_version = await self._resolve_storage_version(artifact_id, record, version)
        return await self._urls.get_download_url(
            org_id=actor.org_id, document_id=record["externalRecordId"], version=storage_version, ttl_s=ttl_s,
        )

    async def _resolve_storage_version(
        self, artifact_id: str, record: dict, version: int | None,
    ) -> int | None:
        """Fetch this artifact's `versions` bookkeeping and delegate the
        actual mapping decision to `versioning.resolve_storage_version` —
        the one place that logic lives (also used directly by the
        connectors' stream route, which already has `versions` in hand and
        skips this graph fetch)."""
        current_version = record.get("version", 1)
        if version is None or version == current_version:
            return None
        artifact_doc = await self._graph_provider.get_document(artifact_id, CollectionNames.ARTIFACTS.value)
        versions = deserialize_artifact_versions((artifact_doc or {}).get("versions"))
        try:
            return resolve_storage_version(current_version, versions, version)
        except VersionMappingNotFoundError as e:
            from .access import ArtifactNotFoundError
            raise ArtifactNotFoundError(str(e)) from e

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
