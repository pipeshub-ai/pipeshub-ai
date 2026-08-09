"""`GraphCheckpointStore`: the first non-in-memory `CheckpointStore` --
backed by `IGraphDBProvider`, works unmodified against either
`ArangoHTTPProvider` or `Neo4jProvider` since it only calls the generic,
backend-agnostic surface of that interface (`get_document`,
`batch_upsert_nodes`, `delete_nodes`, `get_nodes_by_filters`) -- never
`execute_query`. Mirrors the conventions of `GraphSkillStore`
(`app/agents/agent_loop/skills/graph_store.py`), the reference
ABC-over-`IGraphDBProvider` adapter in this codebase.

Why this needs to exist at all: chat requests get away with
`InMemoryCheckpointStore` because a checkpoint only ever needs to survive
the life of one HTTP connection. A scheduled task run has no such
connection -- it must survive a Query-service restart between "paused for
HIL" and "answer arrives" (Part C3 of the task engine plan), and a crash
mid-turn must be resumable from the last `post_tool` checkpoint (Part L's
"first production use of checkpoint/resume code" risk) rather than
silently losing the run.

Document design: `AgentCheckpoint` is entirely re-serialized into one
`content` JSON string per document -- unlike `GraphSkillStore`, there is no
stable, independently-useful set of fields to denormalize (a checkpoint is
consumed by exactly one thing: `load()`/`latest()`/`history()` on this same
class, never queried by content). What IS denormalized, for the four
filter/sort operations this store's own methods need, is `runId`, `orgId`,
and a monotonic `seq` -- `AgentCheckpoint` has no ordinal field of its own
(`turn_index` is not unique within a run: `pre_tool`/`post_tool` checkpoints
at the same turn share a `turn_index`), so `seq` is assigned here, at
save time, as this store's own append-only ordinal.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.modules.stores.checkpoint.base import (
    AgentCheckpoint,
    CheckpointStore,
)
from app.config.constants.arangodb import CollectionNames
from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

__all__ = ["GraphCheckpointStore"]

_CHECKPOINTS = CollectionNames.AGENT_CHECKPOINTS.value


def _doc_id(doc: dict) -> str | None:
    """Backend-agnostic identifier read -- see `GraphSkillStore`'s module
    docstring for why neither backend's raw shape can be trusted alone."""
    return doc.get("id") or doc.get("_key")


class GraphCheckpointStore(CheckpointStore):
    def __init__(self, graph_provider: "IGraphDBProvider", org_id: str) -> None:
        """`org_id` is bound at construction (same convention as
        `GraphSkillStore`) so every write is tenant-tagged even though the
        `CheckpointStore` ABC itself has no org-scoping parameter -- a
        headless run's stores are always built fresh per task run (see
        `services/tasks/runtime/headless_context.py`), never shared across
        orgs."""
        self._graph = graph_provider
        self._org_id = org_id

    async def _docs_for_run(self, run_id: str) -> list[dict]:
        docs = await self._graph.get_nodes_by_filters(
            _CHECKPOINTS, {"orgId": self._org_id, "runId": run_id},
        )
        docs.sort(key=lambda d: d.get("seq", 0))
        return docs

    @staticmethod
    def _doc_to_checkpoint(doc: dict) -> AgentCheckpoint:
        return AgentCheckpoint.model_validate_json(doc["content"])

    def _checkpoint_to_doc(self, checkpoint: AgentCheckpoint, *, seq: int) -> dict[str, Any]:
        return {
            "id": checkpoint.checkpoint_id,
            "orgId": self._org_id,
            "runId": checkpoint.run_id,
            "seq": seq,
            "kind": checkpoint.kind.value,
            "turnIndex": checkpoint.turn_index,
            "createdAtTimestamp": get_epoch_timestamp_in_ms(),
            "content": checkpoint.model_dump_json(),
        }

    async def save(self, checkpoint: AgentCheckpoint) -> str:
        # Sequential by construction: checkpoints for a given run_id are
        # only ever saved from within that run's own single-threaded turn
        # loop (see `agent/observability.py::save_checkpoint`), so reading
        # the current count then writing is race-free in practice --
        # there is exactly one writer per run_id, never concurrent ones.
        existing = await self._docs_for_run(checkpoint.run_id)
        doc = self._checkpoint_to_doc(checkpoint, seq=len(existing))
        await self._graph.batch_upsert_nodes([doc], _CHECKPOINTS)
        return checkpoint.checkpoint_id

    async def load(self, checkpoint_id: str) -> AgentCheckpoint:
        doc = await self._graph.get_document(checkpoint_id, _CHECKPOINTS)
        if doc is None or doc.get("orgId") != self._org_id:
            raise KeyError(checkpoint_id)
        return self._doc_to_checkpoint(doc)

    async def latest(self, run_id: str) -> AgentCheckpoint | None:
        docs = await self._docs_for_run(run_id)
        if not docs:
            return None
        return self._doc_to_checkpoint(docs[-1])

    async def history(self, run_id: str) -> list[AgentCheckpoint]:
        docs = await self._docs_for_run(run_id)
        return [self._doc_to_checkpoint(d) for d in docs]

    async def delete_run(self, run_id: str) -> None:
        docs = await self._docs_for_run(run_id)
        keys = [_doc_id(d) for d in docs if _doc_id(d) is not None]
        if keys:
            await self._graph.delete_nodes(keys, _CHECKPOINTS)
