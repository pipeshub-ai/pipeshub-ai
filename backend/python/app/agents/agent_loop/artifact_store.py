"""Artifact store adapter for tool-result persistence.

Bridges the generic ``ArtifactStore``/``ArtifactReader`` protocols (used by
``shape_artifact_registration``, ``shape_artifact_compaction``, and
``RetrieveArtifactContentTool``) to PipesHub's existing
``ArtifactRegistryService`` — which internally delegates to
``VersionManager.create()`` for blob upload, graph-node creation, and
permission-edge wiring.  No new storage path is introduced; this adapter
simply translates between the simple ``store(content) -> id`` protocol and
the registry's richer interface.

``InMemoryArtifactStore`` serves as an L1 cache for same-request reads so
the blob is only hit on follow-up conversation turns (where the process-
local cache is cold).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.hooks.middleware.builtin.artifact_registration import (
    InMemoryArtifactStore,
)
from app.models.entities import ArtifactType
from app.services.artifact_registry.models import Actor

if TYPE_CHECKING:
    from app.agents.agent_loop.context import AgentContext

logger = logging.getLogger(__name__)


class RegistryBackedStore:
    """L1 ``InMemoryArtifactStore`` + L2 ``ArtifactRegistryService``.

    ``store()`` persists to both — the registry (blob) is the durable source
    of truth, the in-memory layer is a same-request read-through cache.
    ``get()`` checks L1 first and falls back to blob on miss.

    Call path on write::

        store()
          -> ArtifactRegistryService.register()
            -> VersionManager.create()
              -> blob_store.save_versioned_artifact_to_storage()
              -> graph_provider.batch_upsert_nodes()  (records + artifacts)
              -> graph_provider.batch_create_edges()  (permission + is_of_type)

    Call path on read (follow-up turn)::

        get()
          -> InMemoryArtifactStore.get()   [L1 — hit on same request]
          -> ArtifactRegistryService.get_content()  [L2 — blob fallback]
    """

    def __init__(self, registry: Any, actor: Actor, conversation_id: str) -> None:
        self._registry = registry
        self._actor = actor
        self._conversation_id = conversation_id
        self._l1 = InMemoryArtifactStore()

    async def store(
        self,
        content: str,
        *,
        tool_name: str = "",
        result_schema: dict[str, Any] | None = None,
        session_id: str | None = None,
    ) -> str:
        try:
            metadata = await self._registry.register(
                actor=self._actor,
                name=f"tool_result_{tool_name}.json" if tool_name else "tool_result.json",
                artifact_type=ArtifactType.TOOL_RESULT,
                mime_type="application/json",
                content=content.encode("utf-8"),
                conversation_id=self._conversation_id,
                source_tool=tool_name or None,
                is_temporary=True,
                result_schema=result_schema,
            )
        except Exception:
            logger.warning(
                "RegistryBackedStore.store: registry.register() failed for "
                "tool %r — falling back to in-memory only",
                tool_name, exc_info=True,
            )
            return await self._l1.store(
                content, tool_name=tool_name,
                result_schema=result_schema, session_id=session_id,
            )

        aid = metadata.artifact_id
        self._l1._data[aid] = (self._l1._now(), content)
        if result_schema is not None:
            self._l1._schemas[aid] = result_schema
        if tool_name:
            self._l1._tool_names[aid] = tool_name
        return aid

    async def get(self, artifact_id: str) -> str | None:
        result = await self._l1.get(artifact_id)
        if result is not None:
            return result
        try:
            raw = await self._registry.get_content(
                actor=self._actor, artifact_id=artifact_id,
            )
        except Exception:
            return None
        if raw is None:
            return None
        content = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
        self._l1._data[artifact_id] = (self._l1._now(), content)
        return content

    def get_schema(self, artifact_id: str) -> dict[str, Any] | None:
        return self._l1.get_schema(artifact_id)

    def get_tool_name(self, artifact_id: str) -> str | None:
        return self._l1.get_tool_name(artifact_id)


def build_artifact_store(context: "AgentContext") -> Any:
    """Factory: ``ArtifactRegistryService``-backed store in production,
    plain ``InMemoryArtifactStore`` in tests / standalone runs."""
    if context.artifact_registry and context.conversation_id:
        return RegistryBackedStore(
            registry=context.artifact_registry,
            actor=Actor(org_id=context.org_id, user_id=context.user_id),
            conversation_id=context.conversation_id,
        )
    return InMemoryArtifactStore()
