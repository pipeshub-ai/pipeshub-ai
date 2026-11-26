"""Copying artifact versions into another conversation (SS3, "continue privately").

A copy is a new artifact: its own record, its own blob document and the
forker's owner edge, never a reference to the original, so erasing either
conversation (E7) leaves the other's artifacts whole. The version's provenance
is copied with it, so viewers of the copy are checked as viewers of the
original were. The copy's id is derived from the target conversation and the
copied version: a retry finds what an earlier try made.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import CollectionNames
from app.core.org_context import org_of

from .access import AccessDeniedError, ArtifactNotFoundError
from .versioning import to_metadata_from_docs

if TYPE_CHECKING:
    from .models import Actor
    from .registry import ArtifactRegistryService

logger = logging.getLogger(__name__)

__all__ = ["COPY_LIMIT", "ArtifactCopier", "CopyOutcome", "CopyRequest", "copy_artifact_id"]

COPY_LIMIT = 200

_COPY_NAMESPACE = uuid.UUID("5d7bb4b4-0a39-4b8e-9a6f-3c1c2e5f4a11")


@dataclass(frozen=True)
class CopyRequest:
    artifact_id: str
    version: int | None


@dataclass(frozen=True)
class CopyOutcome:
    artifact_id: str
    version: int | None
    copy_id: str | None = None
    document_id: str | None = None
    copy_version: int | None = None
    reason: str | None = None


def copy_artifact_id(org_id: str, to_conversation_id: str, artifact_id: str, version: int | None) -> str:
    """One copy per (org, target conversation, artifact version)."""
    name = "\x00".join((org_id, to_conversation_id, artifact_id, "" if version is None else str(version)))
    return str(uuid.uuid5(_COPY_NAMESPACE, name))


class ArtifactCopier:
    def __init__(self, registry: ArtifactRegistryService, graph_provider: Any) -> None:  # noqa: ANN401 - IGraphDBProvider
        self._registry = registry
        self._graph = graph_provider

    async def copy(
        self, *, actor: Actor, from_conversation_id: str, to_conversation_id: str, refs: list[CopyRequest]
    ) -> list[CopyOutcome]:
        return [await self._one(actor, from_conversation_id, to_conversation_id, ref) for ref in refs]

    async def _one(self, actor: Actor, from_conversation_id: str, to_conversation_id: str, ref: CopyRequest) -> CopyOutcome:
        record: dict[str, Any] | None = await self._graph.get_document(ref.artifact_id, CollectionNames.RECORDS.value)
        artifact: dict[str, Any] | None = await self._graph.get_document(ref.artifact_id, CollectionNames.ARTIFACTS.value)
        if (
            record is None
            or artifact is None
            or org_of(record) != actor.org_id
            or artifact.get("conversationId") != from_conversation_id
        ):
            return self._refused(ref, "not_found")
        version = ref.version if ref.version is not None else int(record.get("version") or 1)
        copy_id = copy_artifact_id(actor.org_id, to_conversation_id, ref.artifact_id, ref.version)
        existing: dict[str, Any] | None = await self._graph.get_document(copy_id, CollectionNames.RECORDS.value)
        if existing is None:
            try:
                content = await self._registry.get_content(actor=actor, artifact_id=ref.artifact_id, version=ref.version)
            except AccessDeniedError:
                return self._refused(ref, "not_permitted")
            except ArtifactNotFoundError:
                return self._refused(ref, "version_missing")
            created = await self._registry.register_copy(
                actor=actor,
                source=to_metadata_from_docs(record, artifact),
                content=content,
                conversation_id=to_conversation_id,
                artifact_id=copy_id,
            )
            document_id = created.document_id or ""
        else:
            document_id = str(existing.get("externalRecordId") or "")
        # Merged per version, so a retry after a crash before it records it once.
        for provenance in await self._registry.lineage.provenance_of(ref.artifact_id):
            if provenance.version == version:
                await self._registry.record_derivation(
                    output_artifact_id=copy_id, output_version=1, provenance=provenance.model_copy(update={"version": 1})
                )
        logger.info(
            "artifacts.copied artifact=%s version=%s copy=%s replayed=%s",
            ref.artifact_id, version, copy_id, existing is not None,
        )
        return CopyOutcome(ref.artifact_id, ref.version, copy_id=copy_id, document_id=document_id, copy_version=1)

    @staticmethod
    def _refused(ref: CopyRequest, reason: str) -> CopyOutcome:
        logger.warning("artifacts.copy_refused artifact=%s reason=%s", ref.artifact_id, reason)
        return CopyOutcome(ref.artifact_id, ref.version, reason=reason)
