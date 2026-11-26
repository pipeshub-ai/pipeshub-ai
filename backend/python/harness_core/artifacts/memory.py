"""In-memory ``ArtifactStore`` and ``PublicationLog`` (tests, Loom embedding)."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from harness_core.artifacts.errors import (
    ArtifactNotWritable,
    ArtifactTooLarge,
    ArtifactVersionConflict,
)
from harness_core.artifacts.types import (
    PublicationHistory,
    StoredArtifact,
    sha256_of,
    union_sources,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from harness_core.artifacts.types import (
        ArtifactCard,
        ArtifactLifecycle,
        Provenance,
        PublicationRecord,
        PublishIntent,
        PublishOutcome,
    )
    from harness_core.interactions.ports import RunBinding

DEFAULT_MAX_BYTES = 100 * 1024 * 1024


@dataclass
class _Artifact:
    artifact_id: str
    name: str
    lifecycle: ArtifactLifecycle
    versions: list[tuple[bytes, str]] = field(default_factory=list[tuple[bytes, str]])
    provenance: dict[int, Provenance] = field(default_factory=dict[int, "Provenance"])
    discarded: bool = False

    def stored(self) -> StoredArtifact:
        content, mime_type = self.versions[-1]
        return StoredArtifact(
            artifact_id=self.artifact_id,
            name=self.name,
            version=len(self.versions),
            sha256=sha256_of(content),
            mime_type=mime_type,
            size_bytes=len(content),
            kind="OTHER",
            lifecycle=self.lifecycle,
        )


class InMemoryArtifactStore:
    """Artifacts per (tenant, scope); every run of the scope may write them."""

    def __init__(self, *, max_bytes: int = DEFAULT_MAX_BYTES) -> None:
        self._max_bytes = max_bytes
        self._scopes: dict[tuple[str, str], dict[str, _Artifact]] = {}

    def listing(self, binding: RunBinding) -> list[StoredArtifact]:
        """Every live artifact of the binding's scope (what a host's artifacts panel would list)."""
        return [a.stored() for a in self._artifacts(binding).values() if not a.discarded]

    async def find_by_name(self, binding: RunBinding, name: str) -> StoredArtifact | None:
        found = self._named(binding, name)
        return found.stored() if found is not None else None

    async def current(self, binding: RunBinding, artifact_id: str) -> StoredArtifact:
        return self._live(binding, artifact_id).stored()

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
        self._check_size(content)
        artifacts = self._artifacts(binding)
        existing = artifacts.get(artifact_id)
        if existing is None or existing.discarded:
            existing = self._named(binding, name)
        if existing is None:
            created = _Artifact(artifact_id, name, lifecycle, [(content, mime_type)])
            artifacts[artifact_id] = created
            return created.stored(), "created"
        return self._bump(existing, content, mime_type, lifecycle)

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
        self._check_size(content)
        artifact = self._live(binding, artifact_id)
        if len(artifact.versions) != expected_version:
            raise ArtifactVersionConflict(artifact_id, expected=expected_version, current=len(artifact.versions))
        return self._bump(artifact, content, mime_type, lifecycle)

    async def content(self, binding: RunBinding, artifact_id: str, version: int) -> bytes:
        artifact = self._live(binding, artifact_id)
        if not 1 <= version <= len(artifact.versions):
            raise ArtifactNotWritable(f"artifact {artifact_id} has no version {version}")
        return artifact.versions[version - 1][0]

    async def set_lifecycle(self, binding: RunBinding, artifact_id: str, lifecycle: ArtifactLifecycle) -> None:
        self._live(binding, artifact_id).lifecycle = lifecycle

    async def discard(self, binding: RunBinding, artifact_id: str) -> None:
        artifact = self._live(binding, artifact_id)
        artifact.discarded = True
        artifact.lifecycle = "discarded"

    async def record_provenance(
        self, binding: RunBinding, artifact_id: str, version: int, provenance: Provenance
    ) -> None:
        artifact = self._artifacts(binding)[artifact_id]
        earlier = artifact.provenance.get(version)
        if earlier is not None:
            provenance = replace(
                provenance, source_records=union_sources(earlier.source_records, provenance.source_records)
            )
        artifact.provenance[version] = provenance

    async def sources_of(
        self, binding: RunBinding, artifact_ids: Sequence[str]
    ) -> Mapping[str, frozenset[str] | None]:
        artifacts = self._artifacts(binding)
        answer: dict[str, frozenset[str] | None] = {}
        for artifact_id in artifact_ids:
            artifact = artifacts.get(artifact_id)
            if artifact is None or artifact.discarded:
                answer[artifact_id] = None
                continue
            recorded = [artifact.provenance.get(v) for v in range(1, len(artifact.versions) + 1)]
            answer[artifact_id] = (
                None if any(p is None for p in recorded)
                else union_sources(*(p.source_records for p in recorded if p is not None))
            )
        return answer

    def _bump(
        self, artifact: _Artifact, content: bytes, mime_type: str, lifecycle: ArtifactLifecycle
    ) -> tuple[StoredArtifact, PublishOutcome]:
        if sha256_of(artifact.versions[-1][0]) == sha256_of(content):
            return artifact.stored(), "unchanged"
        artifact.versions.append((content, mime_type))
        artifact.lifecycle = lifecycle
        return artifact.stored(), "versioned"

    def _artifacts(self, binding: RunBinding) -> dict[str, _Artifact]:
        return self._scopes.setdefault((binding.tenant, binding.scope), {})

    def _named(self, binding: RunBinding, name: str) -> _Artifact | None:
        return next(
            (a for a in self._artifacts(binding).values() if a.name == name and not a.discarded), None
        )

    def _live(self, binding: RunBinding, artifact_id: str) -> _Artifact:
        artifact = self._artifacts(binding).get(artifact_id)
        if artifact is None or artifact.discarded:
            raise ArtifactNotWritable(f"artifact {artifact_id} is not writable here")
        return artifact

    def _check_size(self, content: bytes) -> None:
        if len(content) > self._max_bytes:
            raise ArtifactTooLarge(f"{len(content)} bytes exceeds the {self._max_bytes}-byte cap")


class InMemoryPublicationLog:
    """Per run, for the life of the process."""

    def __init__(self) -> None:
        self._runs: dict[tuple[str, str, str], PublicationHistory] = {}

    async def intent(self, binding: RunBinding, intent: PublishIntent) -> None:
        history = self._history(binding)
        self._runs[_key(binding)] = replace(history, intents=(*history.intents, intent))

    async def published(self, binding: RunBinding, record: PublicationRecord) -> None:
        history = self._history(binding)
        self._runs[_key(binding)] = replace(history, published=(*history.published, record))

    async def presented(self, binding: RunBinding, card: ArtifactCard) -> None:
        history = self._history(binding)
        self._runs[_key(binding)] = replace(history, cards=(*history.cards, card))

    async def history(self, binding: RunBinding) -> PublicationHistory:
        return self._history(binding)

    def _history(self, binding: RunBinding) -> PublicationHistory:
        return self._runs.get(_key(binding), PublicationHistory())


def _key(binding: RunBinding) -> tuple[str, str, str]:
    return (binding.tenant, binding.scope, binding.run_id)


__all__ = ["DEFAULT_MAX_BYTES", "InMemoryArtifactStore", "InMemoryPublicationLog"]
