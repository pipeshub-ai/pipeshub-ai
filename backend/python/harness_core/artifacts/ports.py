"""Artifact publication ports (ARCHITECTURE §4.7).

``ArtifactPublisher`` is what an engine controller calls at a save point.
``ArtifactStore`` is the host's artifact registry; ``PublicationLog`` is where
the publication events go (the run's journal for a durable run). Ids are opaque: a tenant, a scope (the session) and a run.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from harness_core.artifacts.types import (
        ArtifactCard,
        ArtifactLifecycle,
        Derivation,
        OutputFile,
        Provenance,
        PublicationHistory,
        PublicationRecord,
        PublishIntent,
        PublishOutcome,
        PublishReport,
        SettleReport,
        StoredArtifact,
    )
    from harness_core.interactions.ports import RunBinding
    from harness_core.workspace.types import WorkspaceManifest


class ArtifactPublisher(Protocol):
    async def publish_changes(
        self,
        binding: RunBinding,
        version: WorkspaceManifest,
        outputs: Sequence[OutputFile],
        *,
        draft: bool,
        derivation: Derivation,
    ) -> PublishReport:
        """Publish each output once per (run, path, content); a refused output never stops the others."""
        ...

    async def prepare(self, binding: RunBinding, outputs: Sequence[OutputFile]) -> None:
        """Log each output's intent before its bytes are committed anywhere a crash could leave them.

        Paired with ``publish_pending``: an intent with no publication whose content is still in the
        workspace is a save the run committed and never published.
        """
        ...

    async def publish_pending(
        self,
        binding: RunBinding,
        version: WorkspaceManifest,
        outputs: Sequence[OutputFile],
        *,
        draft: bool,
        derivation: Derivation,
    ) -> PublishReport:
        """Publish the outputs whose intent this run logged and never published; nothing else."""
        ...

    async def finalize(self, binding: RunBinding, *, keep_drafts: frozenset[str] = frozenset()) -> SettleReport:
        """The run's open drafts become final (task completed), but those named in ``keep_drafts`` (WP-70: a
        placeholder the run still owes stays a draft). Idempotent."""
        ...

    async def discard_drafts(self, binding: RunBinding) -> SettleReport:
        """The run's open drafts are discarded (task cancelled). Idempotent."""
        ...


class ArtifactStore(Protocol):
    """The host's versioned artifacts of one scope; writes are authorized as the run's actor."""

    async def find_by_name(self, binding: RunBinding, name: str) -> StoredArtifact | None: ...

    async def current(self, binding: RunBinding, artifact_id: str) -> StoredArtifact:
        """``ArtifactNotWritable`` when missing, discarded or not writable by the run's actor."""
        ...

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
        """A new artifact with ``artifact_id``, or a version of the one named ``name`` in the scope.

        An existing ``artifact_id`` (a retry) is returned, never created twice.
        """
        ...

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
        """``ArtifactVersionConflict`` unless the artifact is at ``expected_version``; equal bytes add nothing."""
        ...

    async def content(self, binding: RunBinding, artifact_id: str, version: int) -> bytes: ...

    async def set_lifecycle(
        self, binding: RunBinding, artifact_id: str, lifecycle: ArtifactLifecycle
    ) -> None: ...

    async def discard(self, binding: RunBinding, artifact_id: str) -> None:
        """Removes a draft the run created: it is no longer found by name or id."""
        ...

    async def record_provenance(
        self, binding: RunBinding, artifact_id: str, version: int, provenance: Provenance
    ) -> None:
        """Re-recording a version merges: the union of sources, and unknown wins."""
        ...

    async def sources_of(
        self, binding: RunBinding, artifact_ids: Sequence[str]
    ) -> Mapping[str, frozenset[str] | None]:
        """Source records over every version; ``None`` when any version's are not known."""
        ...


class PublicationLog(Protocol):
    """The run's publication events: exactly what intent, published and presented carry."""

    async def intent(self, binding: RunBinding, intent: PublishIntent) -> None: ...

    async def published(self, binding: RunBinding, record: PublicationRecord) -> None: ...

    async def presented(self, binding: RunBinding, card: ArtifactCard) -> None: ...

    async def history(self, binding: RunBinding) -> PublicationHistory: ...


__all__ = ["ArtifactPublisher", "ArtifactStore", "PublicationLog"]
