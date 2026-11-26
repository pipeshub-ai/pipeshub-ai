"""``RegistryArtifactStore``: the publisher's ``ArtifactStore`` over ``ArtifactRegistryService``.

One per run, as the run's actor: a binding's tenant is the org and its scope
the conversation. Every write goes through the registry (blob first, then
graph, owner checks, version CAS), so engine artifacts are the same records,
cards and downloads as every other producer's.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from app.config.constants.arangodb import Connectors
from app.models.entities import LifecycleStatus
from app.sandbox.artifact_upload import infer_artifact_type
from app.services.artifact_registry.access import (
    AccessDeniedError,
    ArtifactNotFoundError,
)
from app.services.artifact_registry.models import ArtifactProvenance, ArtifactVersionPin
from app.services.artifact_registry.versioning import VersionConflictError
from harness_core.artifacts.errors import (
    ArtifactNotWritable,
    ArtifactTooLarge,
    ArtifactVersionConflict,
)
from harness_core.artifacts.types import StoredArtifact

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from app.services.artifact_registry.models import (
        Actor,
        ArtifactMetadata,
        ArtifactVersion,
    )
    from app.services.artifact_registry.registry import ArtifactRegistryService
    from harness_core.artifacts.types import (
        ArtifactLifecycle,
        Provenance,
        PublishOutcome,
    )
    from harness_core.interactions.ports import RunBinding

SOURCE_TOOL = "harness.artifact_publisher"

_TO_STATUS: dict[ArtifactLifecycle, LifecycleStatus] = {
    "draft": LifecycleStatus.DRAFT,
    "final": LifecycleStatus.PUBLISHED,
    "discarded": LifecycleStatus.REJECTED,
}
_FROM_STATUS: dict[LifecycleStatus, ArtifactLifecycle] = {
    LifecycleStatus.DRAFT: "draft",
    LifecycleStatus.REJECTED: "discarded",
}


def _stored(metadata: ArtifactMetadata) -> StoredArtifact:
    return StoredArtifact(
        artifact_id=metadata.artifact_id,
        name=metadata.logical_name,
        version=metadata.version,
        sha256=f"sha256:{metadata.content_hash}" if metadata.content_hash else "",
        mime_type=metadata.mime_type,
        size_bytes=metadata.size_bytes,
        kind=metadata.artifact_type.value,
        lifecycle=_FROM_STATUS.get(metadata.lifecycle_status, "final"),
        storage_ref=metadata.document_id,
    )


def _outcome(version: ArtifactVersion | None) -> PublishOutcome:
    if version is None:
        return "created"
    return "unchanged" if version.deduplicated else "versioned"


class RegistryArtifactStore:
    def __init__(self, registry: ArtifactRegistryService, actor: Actor) -> None:
        self._registry = registry
        self._actor = actor

    async def find_by_name(self, binding: RunBinding, name: str) -> StoredArtifact | None:
        self._check(binding)
        found = await self._registry.resolve_by_logical_name(self._actor, name, conversation_id=binding.scope)
        return _stored(found) if found is not None else None

    async def current(self, binding: RunBinding, artifact_id: str) -> StoredArtifact:
        return _stored(await self._writable(binding, artifact_id))

    async def register_output(
        self,
        binding: RunBinding,
        *,
        artifact_id: str,
        name: str,
        mime_type: str,
        content: bytes,
        lifecycle: ArtifactLifecycle,
    ) -> tuple[StoredArtifact, PublishOutcome]:
        self._check(binding)
        self._check_size(content)
        try:
            metadata, version = await self._registry.register_output(
                actor=self._actor, name=name, artifact_type=infer_artifact_type(mime_type),
                mime_type=mime_type, content=content, conversation_id=binding.scope,
                source_tool=SOURCE_TOOL, connector_name=Connectors.CODING_SANDBOX,
                artifact_id=artifact_id, lifecycle_status=_TO_STATUS[lifecycle],
            )
        except VersionConflictError as exc:
            # Only after the registry's own bounded retry: another writer keeps moving it.
            raise ArtifactVersionConflict(artifact_id, expected=None, current=None) from exc
        except (ArtifactNotFoundError, AccessDeniedError) as exc:
            raise ArtifactNotWritable(f"artifact named in scope {binding.scope} is not writable") from exc
        return _stored(metadata), _outcome(version)

    async def add_version(
        self,
        binding: RunBinding,
        artifact_id: str,
        *,
        content: bytes,
        mime_type: str,
        expected_version: int,
        lifecycle: ArtifactLifecycle,
    ) -> tuple[StoredArtifact, PublishOutcome]:
        await self._writable(binding, artifact_id)
        self._check_size(content)
        try:
            version, metadata = await self._registry.add_version(
                actor=self._actor, artifact_id=artifact_id, content=content, mime_type=mime_type,
                expected_version=expected_version,
            )
        except VersionConflictError as exc:
            current = await self._writable(binding, artifact_id)
            raise ArtifactVersionConflict(artifact_id, expected=expected_version, current=current.version) from exc
        outcome = _outcome(version)
        if outcome == "versioned":
            metadata = await self._registry.set_lifecycle(
                actor=self._actor, artifact_id=artifact_id, lifecycle_status=_TO_STATUS[lifecycle],
            )
        return _stored(metadata), outcome

    async def content(self, binding: RunBinding, artifact_id: str, version: int) -> bytes:
        await self._writable(binding, artifact_id)
        return await self._registry.get_content(actor=self._actor, artifact_id=artifact_id, version=version)

    async def set_lifecycle(self, binding: RunBinding, artifact_id: str, lifecycle: ArtifactLifecycle) -> None:
        await self._writable(binding, artifact_id)
        _ = await self._registry.set_lifecycle(
            actor=self._actor, artifact_id=artifact_id, lifecycle_status=_TO_STATUS[lifecycle],
        )

    async def discard(self, binding: RunBinding, artifact_id: str) -> None:
        await self._writable(binding, artifact_id)
        await self._registry.discard(actor=self._actor, artifact_id=artifact_id)

    async def record_provenance(
        self, binding: RunBinding, artifact_id: str, version: int, provenance: Provenance
    ) -> None:
        self._check(binding)
        await self._registry.record_derivation(
            output_artifact_id=artifact_id,
            output_version=version,
            provenance=ArtifactProvenance(
                version=version,
                run_id=provenance.run_id,
                task_id=provenance.task_id,
                workspace_id=provenance.workspace_id,
                workspace_revision=provenance.workspace_revision,
                command=provenance.command,
                input_paths=list(provenance.input_paths),
                input_artifacts=[
                    ArtifactVersionPin(artifact_id=ref.artifact_id, version=ref.version)
                    for ref in provenance.input_artifacts
                ],
                source_record_ids=(
                    sorted(provenance.source_records) if provenance.source_records is not None else None
                ),
            ),
        )

    async def sources_of(
        self, binding: RunBinding, artifact_ids: Sequence[str]
    ) -> Mapping[str, frozenset[str] | None]:
        self._check(binding)
        return await self._registry.lineage.sources_of(self._actor.org_id, artifact_ids)

    async def _writable(self, binding: RunBinding, artifact_id: str) -> ArtifactMetadata:
        """Owned by the actor, live, and of this conversation: an engine never edits another's artifact."""
        self._check(binding)
        try:
            record = cast(
                "dict[str, object]",
                await self._registry.access.authorize_write(self._actor, artifact_id),  # pyright: ignore[reportUnknownMemberType]
            )
            metadata = await self._registry.resolve(actor=self._actor, ref=artifact_id)
        except (ArtifactNotFoundError, AccessDeniedError) as exc:
            raise ArtifactNotWritable(f"artifact {artifact_id} is not writable") from exc
        if record.get("isDeleted") or metadata.conversation_id != binding.scope:
            raise ArtifactNotWritable(f"artifact {artifact_id} is not writable")
        return metadata

    def _check(self, binding: RunBinding) -> None:
        if binding.tenant != self._actor.org_id:
            raise ArtifactNotWritable("the run's tenant is not the store's org")

    def _check_size(self, content: bytes) -> None:
        if len(content) > self._registry.max_bytes:
            raise ArtifactTooLarge(f"{len(content)} bytes exceeds the {self._registry.max_bytes}-byte cap")


__all__ = ["SOURCE_TOOL", "RegistryArtifactStore"]
