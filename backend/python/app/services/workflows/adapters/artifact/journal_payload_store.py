"""`ArtifactJournalPayloadStore` -- `IJournalPayloadStore` over the artifact
registry, so spilled journal results land in the same object storage every
other large payload in the platform already uses.

Registered STAGING and temporary: these exist for replay, not for the user,
and must never appear in a conversation's artifact list.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.config.constants.arangodb import Connectors
from app.models.entities import ArtifactType, ArtifactVisibility
from app.services.artifact_registry import Actor, ArtifactRegistryService
from app.services.workflows.domain.models import ArtifactRef

if TYPE_CHECKING:
    from app.modules.transformers.blob_storage import BlobStorage
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

__all__ = ["ArtifactJournalPayloadStore"]

logger = logging.getLogger(__name__)

_MIME_TYPE = "application/json"


class ArtifactJournalPayloadStore:
    def __init__(
        self,
        *,
        graph_provider: "IGraphDBProvider",
        blob_store: "BlobStorage",
        org_id: str,
        user_id: str,
        conversation_id: str | None = None,
    ) -> None:
        # Scoped to one run's principal rather than taking an `Actor` per
        # call: `IJournalPayloadStore` is deliberately identity-free so the
        # journal decorator cannot be handed the wrong tenant's payloads.
        self._registry = ArtifactRegistryService(graph_provider, blob_store)
        self._actor = Actor(org_id=org_id, user_id=user_id)
        self._conversation_id = conversation_id

    async def put(self, *, run_id: str, step_key: str, payload: bytes) -> ArtifactRef:
        metadata = await self._registry.register(
            actor=self._actor,
            name=f"journal-{run_id}-{_safe_name(step_key)}.json",
            artifact_type=ArtifactType.TOOL_RESULT,
            mime_type=_MIME_TYPE,
            content=payload,
            conversation_id=self._conversation_id,
            description=f"Spilled workflow journal result for step {step_key}",
            source_tool="workflow_journal",
            is_temporary=True,
            visibility=ArtifactVisibility.STAGING,
            connector_name=Connectors.CODING_SANDBOX,
        )
        return ArtifactRef(artifact_id=metadata.artifact_id, version=str(metadata.version))

    async def get(self, ref: ArtifactRef) -> bytes | None:
        version = _parse_version(ref.version)
        try:
            return await self._registry.get_content(
                actor=self._actor, artifact_id=ref.artifact_id, version=version,
            )
        except Exception:
            logger.exception("journal payload store: cannot read artifact %s", ref.artifact_id)
            return None


def _safe_name(step_key: str) -> str:
    """Step keys carry `/` from nested scopes, which would read as a path."""
    return "".join(char if char.isalnum() or char in "-_" else "-" for char in step_key)[:64]


def _parse_version(version: str | None) -> int | None:
    """`ArtifactRef.version` is a string, the registry counts in ints."""
    if version is None:
        return None
    try:
        return int(version)
    except ValueError:
        return None
