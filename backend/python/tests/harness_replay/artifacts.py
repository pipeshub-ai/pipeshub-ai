"""An in-memory stand-in for ``ArtifactRegistryService`` for replay and parity runs.

The legacy route builds a registry per request (``AgentContext.artifact_registry``)
over the graph and blob store. A run driven by :mod:`legacy_driver` has
neither, so this keeps every artifact (and every version's bytes) in memory:
``run_code`` outputs land here exactly as they would in the product, and
seeded files (an earlier upload) are resolved by ``input_artifacts`` the
same way. Only the surface the agent loop calls is implemented, plus what an
engine run's ``RegistryArtifactStore`` calls (versions, lifecycle, write checks),
so engine drafts and finals land here as they would in the product.
"""

from __future__ import annotations

import hashlib
import mimetypes
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.models.entities import ArtifactType, ArtifactVisibility, LifecycleStatus
from app.services.artifact_registry.access import ArtifactNotFoundError
from app.services.artifact_registry.models import ArtifactMetadata, ArtifactVersion

if TYPE_CHECKING:
    from app.services.artifact_registry.models import Actor

_TYPE_BY_EXTENSION: dict[str, ArtifactType] = {
    ".csv": ArtifactType.DATA_FILE,
    ".json": ArtifactType.DATA_FILE,
    ".xlsx": ArtifactType.SPREADSHEET,
    ".docx": ArtifactType.DOCUMENT,
    ".md": ArtifactType.DOCUMENT,
    ".pdf": ArtifactType.DOCUMENT,
    ".pptx": ArtifactType.PRESENTATION,
    ".png": ArtifactType.IMAGE,
}


@dataclass(frozen=True)
class StoredFile:
    name: str
    mime_type: str
    content: bytes
    version: int
    visibility: ArtifactVisibility
    seeded: bool


@dataclass
class _Entry:
    metadata: ArtifactMetadata
    versions: list[bytes] = field(default_factory=list[bytes])
    seeded: bool = False


class _Access:
    def __init__(self, registry: ParityArtifactRegistry) -> None:
        self._registry = registry

    async def authorize_write(self, actor: Actor, artifact_id: str) -> dict[str, object]:
        self._registry._get(artifact_id)  # pyright: ignore[reportPrivateUsage]
        return {"isDeleted": False}


class _Lineage:
    async def sources_of(self, org_id: str, artifact_ids: object) -> dict[str, frozenset[str] | None]:
        return {}


class ParityArtifactRegistry:
    """One conversation's artifacts. Ids are sequential, so runs are reproducible."""

    max_bytes = 50 * 1024 * 1024

    def __init__(self, org_id: str, conversation_id: str) -> None:
        self._org_id = org_id
        self._conversation_id = conversation_id
        self._entries: dict[str, _Entry] = {}
        self.access = _Access(self)
        self.lineage = _Lineage()

    def __call__(self, *_: object, **__: object) -> ParityArtifactRegistry:
        # Patched in place of the ``ArtifactRegistryService`` class: the
        # context constructs one per access, and every access must see this store.
        return self

    def seed(self, name: str, content: bytes, mime_type: str | None = None) -> ArtifactMetadata:
        metadata = self._create(
            name=name,
            mime_type=mime_type or _guess_mime(name),
            content=content,
            visibility=ArtifactVisibility.VISIBLE,
            source_tool=None,
            artifact_type=_type_for(name),
        )
        self._entries[metadata.artifact_id].seeded = True
        return metadata

    def files(self) -> list[StoredFile]:
        return [
            StoredFile(
                name=e.metadata.name,
                mime_type=e.metadata.mime_type,
                content=e.versions[-1],
                version=e.metadata.version,
                visibility=e.metadata.visibility,
                seeded=e.seeded,
            )
            for e in self._entries.values()
        ]

    def history(self) -> dict[str, tuple[bytes, ...]]:
        """Every live artifact's versions, oldest first, by name (seeded ones too)."""
        discarded = self._discarded()
        return {
            e.metadata.logical_name: tuple(e.versions)
            for e in self._entries.values()
            if e.metadata.logical_name not in discarded
        }

    def outputs(self) -> list[StoredFile]:
        """Files the run delivered to the user: visible and not seeded."""
        return [
            f
            for f in self.files()
            if f.visibility == ArtifactVisibility.VISIBLE and not f.seeded and f.name not in self._discarded()
        ]

    def _discarded(self) -> set[str]:
        return {
            e.metadata.name for e in self._entries.values() if e.metadata.lifecycle_status == LifecycleStatus.REJECTED
        }

    def _create(
        self,
        *,
        name: str,
        mime_type: str,
        content: bytes,
        visibility: ArtifactVisibility,
        source_tool: str | None,
        artifact_type: ArtifactType,
        description: str = "",
        artifact_id: str | None = None,
        lifecycle_status: LifecycleStatus | None = None,
    ) -> ArtifactMetadata:
        artifact_id = artifact_id or f"art-{len(self._entries) + 1:04d}"
        metadata = ArtifactMetadata(
            artifact_id=artifact_id,
            org_id=self._org_id,
            conversation_id=self._conversation_id,
            name=name,
            logical_name=name,
            artifact_type=artifact_type,
            mime_type=mime_type,
            description=description,
            version=1,
            size_bytes=len(content),
            content_hash=_sha(content),
            source_tool=source_tool,
            document_id=f"doc-{artifact_id}",
            visibility=visibility,
            created_at=_now_ms(),
            updated_at=_now_ms(),
            **({"lifecycle_status": lifecycle_status} if lifecycle_status is not None else {}),
        )
        self._entries[artifact_id] = _Entry(metadata=metadata, versions=[content])
        return metadata

    def _by_name(self, name: str) -> _Entry | None:
        for entry in self._entries.values():
            if entry.metadata.logical_name == name:
                return entry
        return None

    def _get(self, artifact_id: str) -> _Entry:
        entry = self._entries.get(artifact_id)
        if entry is None:
            raise ArtifactNotFoundError(f"No artifact {artifact_id!r}")
        return entry

    async def register(
        self,
        *,
        actor: Actor,
        name: str,
        artifact_type: ArtifactType,
        mime_type: str,
        content: bytes,
        conversation_id: str | None,
        description: str = "",
        source_tool: str | None = None,
        visibility: ArtifactVisibility = ArtifactVisibility.VISIBLE,
        **_: object,
    ) -> ArtifactMetadata:
        return self._create(
            name=name,
            mime_type=mime_type,
            content=content,
            visibility=visibility,
            source_tool=source_tool,
            artifact_type=artifact_type,
            description=description,
        )

    async def register_output(
        self,
        *,
        actor: Actor,
        name: str,
        artifact_type: ArtifactType,
        mime_type: str,
        content: bytes,
        conversation_id: str,
        description: str = "",
        source_tool: str | None = None,
        visibility: ArtifactVisibility = ArtifactVisibility.VISIBLE,
        lifecycle_status: LifecycleStatus | None = None,
        artifact_id: str | None = None,
        **_: object,
    ) -> tuple[ArtifactMetadata, ArtifactVersion | None]:
        existing = self._by_name(name)
        if existing is None:
            created = self._create(
                name=name,
                mime_type=mime_type,
                content=content,
                visibility=visibility,
                source_tool=source_tool,
                artifact_type=artifact_type,
                description=description,
                artifact_id=artifact_id,
                lifecycle_status=lifecycle_status,
            )
            return created, None
        deduplicated = existing.metadata.content_hash == _sha(content)
        if not deduplicated:
            existing.versions.append(content)
        existing.metadata = existing.metadata.model_copy(
            update={
                "version": len(existing.versions),
                "size_bytes": len(content),
                "content_hash": _sha(content),
                "mime_type": mime_type,
                **({} if deduplicated else {"updated_at": _now_ms()}),
                "visibility": ArtifactVisibility.VISIBLE
                if visibility == ArtifactVisibility.VISIBLE
                else existing.metadata.visibility,
            }
        )
        version = ArtifactVersion(
            version=existing.metadata.version,
            size_bytes=len(content),
            content_hash=_sha(content),
            mime_type=mime_type,
            created_at=0,
            deduplicated=deduplicated,
        )
        return existing.metadata, version

    async def record_derivation(self, **_: object) -> None:
        return None

    async def add_version(
        self, *, actor: Actor, artifact_id: str, content: bytes, mime_type: str | None = None, expected_version: int = 0,
        **_: object,
    ) -> tuple[ArtifactVersion | None, ArtifactMetadata]:
        entry = self._get(artifact_id)
        metadata, version = await self.register_output(
            actor=actor, name=entry.metadata.logical_name, artifact_type=entry.metadata.artifact_type,
            # As the registry's ``add_version``: no type given keeps the artifact's (FIX-P2-T9).
            mime_type=mime_type or entry.metadata.mime_type, content=content, conversation_id=self._conversation_id,
        )
        return version, metadata

    async def set_lifecycle(self, *, actor: Actor, artifact_id: str, lifecycle_status: LifecycleStatus) -> ArtifactMetadata:
        entry = self._get(artifact_id)
        entry.metadata = entry.metadata.model_copy(update={"lifecycle_status": lifecycle_status})
        return entry.metadata

    async def discard(self, *, actor: Actor, artifact_id: str) -> None:
        await self.set_lifecycle(actor=actor, artifact_id=artifact_id, lifecycle_status=LifecycleStatus.REJECTED)

    async def get_download_url(self, *, actor: Actor, artifact_id: str, **_: object) -> str:
        self._get(artifact_id)
        return f"https://storage.test/artifacts/{artifact_id}"

    async def resolve(
        self, *, actor: Actor, ref: str, conversation_id: str | None = None
    ) -> ArtifactMetadata:
        if ref in self._entries:
            return self._entries[ref].metadata
        entry = self._by_name(ref)
        if entry is None and "." not in ref:
            entry = next(
                (
                    e
                    for e in self._entries.values()
                    if e.metadata.logical_name.rsplit(".", 1)[0] == ref
                ),
                None,
            )
        if entry is None:
            raise ArtifactNotFoundError(f"No artifact named {ref!r} in this conversation")
        return entry.metadata

    async def resolve_by_logical_name(
        self, actor: Actor, name: str, *, conversation_id: str
    ) -> ArtifactMetadata | None:
        entry = self._by_name(name)
        return entry.metadata if entry else None

    async def get_content(
        self, *, actor: Actor, artifact_id: str, version: int | None = None
    ) -> bytes:
        entry = self._get(artifact_id)
        index = (version or len(entry.versions)) - 1
        if not 0 <= index < len(entry.versions):
            raise ArtifactNotFoundError(f"{artifact_id} has no version {version}")
        return entry.versions[index]

    async def list_for_conversation(
        self,
        *,
        actor: Actor,
        conversation_id: str,
        include_lineage: bool = True,
        limit: int = 100,
        visibility_filter: ArtifactVisibility | None = None,
    ) -> list[ArtifactMetadata]:
        listed = [
            e.metadata
            for e in self._entries.values()
            if visibility_filter is None or e.metadata.visibility == visibility_filter
        ]
        return listed[:limit]

    async def promote_to_visible(self, *, actor: Actor, artifact_id: str) -> ArtifactMetadata:
        entry = self._get(artifact_id)
        entry.metadata = entry.metadata.model_copy(
            update={"visibility": ArtifactVisibility.VISIBLE}
        )
        return entry.metadata


def _now_ms() -> int:
    """Wall-clock milliseconds, as the registry stamps ``updatedAtTimestamp``."""
    return int(time.time() * 1000)


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _guess_mime(name: str) -> str:
    return mimetypes.guess_type(name)[0] or "application/octet-stream"


def _type_for(name: str) -> ArtifactType:
    suffix = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return _TYPE_BY_EXTENSION.get(suffix, ArtifactType.OTHER)


__all__ = ["ParityArtifactRegistry", "StoredFile"]
