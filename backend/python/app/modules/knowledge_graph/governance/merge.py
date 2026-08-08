"""Manual entity merge (KG Clean Rebuild plan, Phase 7 / Part E governance
"merge"): an admin-triggered counterpart to the automatic hard-key/canonical
auto-merge and LLM-adjudicated merge already performed inline during
resolution (see ``indexing/resolution.py``). Used to fix a duplicate the
automatic pipeline missed (e.g. two "person" nodes below the soft-similarity
threshold that a human recognizes as the same individual).

Soft-merge only: the duplicate node is never deleted. Its bi-temporal edges
are redirected onto the survivor (invalidate-old, write-new — reusing
``BitemporalGraphWriter`` from Phase 6, so the redirect is itself auditable
and ``as_of``-queryable) and the node itself is tagged ``mergedInto`` rather
than removed, preserving provenance and any existing vector-store /
extraction-envelope references that still point at its id.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.modules.knowledge_graph.indexing.temporal import BitemporalGraphWriter, NodeRef
from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    import logging

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider


class MergeError(Exception):
    """Raised when a merge cannot proceed (bad input, not found)."""


class EntityMergeService:
    """Redirects a duplicate node's bi-temporal edges onto a survivor and
    marks the duplicate merged. Idempotent: re-running against an
    already-merged duplicate is a no-op (edges have already moved; the
    ``mergedInto`` tag is simply rewritten to the same value).
    """

    def __init__(self, graph_provider: "IGraphDBProvider", logger: logging.Logger) -> None:
        self.graph_provider = graph_provider
        self.logger = logger
        self.writer = BitemporalGraphWriter(graph_provider, logger)

    async def merge(
        self,
        org_id: str,
        survivor: NodeRef,
        duplicate: NodeRef,
        *,
        reason: str = "",
        merged_by: str | None = None,
    ) -> dict[str, Any]:
        """Merge ``duplicate`` into ``survivor``. Returns a summary dict with
        ``edgesRedirected`` and ``duplicateNodeId``.

        Raises :class:`MergeError` if ``org_id`` is missing or the two nodes
        are identical (nothing to merge).
        """
        if not org_id:
            raise MergeError("org_id is required")
        if survivor.node_id == duplicate.node_id and survivor.collection == duplicate.collection:
            raise MergeError("survivor and duplicate refer to the same node")

        redirected = 0
        redirected += await self._redirect_edges(org_id, duplicate, survivor, as_subject=True)
        redirected += await self._redirect_edges(org_id, duplicate, survivor, as_subject=False)

        now = get_epoch_timestamp_in_ms()
        await self.graph_provider.update_node(
            key=duplicate.node_id,
            collection=duplicate.collection,
            node_updates={
                "mergedInto": survivor.node_id,
                "mergedIntoCollection": survivor.collection,
                "mergedAtTimestamp": now,
                "mergedBy": merged_by,
                "mergeReason": reason,
            },
        )

        self.logger.info(
            "EntityMergeService.merge: org=%s duplicate=%s -> survivor=%s (%d edges redirected)",
            org_id, duplicate, survivor, redirected,
        )
        return {
            "survivorNodeId": survivor.node_id,
            "duplicateNodeId": duplicate.node_id,
            "edgesRedirected": redirected,
        }

    async def _redirect_edges(
        self, org_id: str, duplicate: NodeRef, survivor: NodeRef, *, as_subject: bool,
    ) -> int:
        """Move every currently-active bi-temporal edge where ``duplicate``
        is the subject (``as_subject=True``) or object onto ``survivor``,
        preserving the edge's type and attributes.
        """
        edges = await self.writer.get_as_of(
            org_id,
            subject=duplicate if as_subject else None,
            obj=duplicate if not as_subject else None,
            include_history=False,
            limit=500,
        )
        moved = 0
        for edge in edges:
            other_node_id = edge.get("toId") if as_subject else edge.get("fromId")
            other_collection = edge.get("toCollection") if as_subject else edge.get("fromCollection")
            if not other_node_id or not other_collection:
                continue
            # A self-referential edge that would become survivor<->survivor
            # once redirected is dropped rather than written as a self-loop.
            if other_node_id == survivor.node_id and other_collection == survivor.collection:
                continue
            edge_type = edge.get("edgeType")
            if not edge_type:
                continue
            attributes = edge.get("attributes") or {}
            other_ref = NodeRef(other_node_id, other_collection)
            new_subject = survivor if as_subject else other_ref
            new_object = other_ref if as_subject else survivor
            try:
                await self.writer.write_edge(org_id, new_subject, new_object, edge_type, attributes=attributes)
                moved += 1
            except Exception as exc:
                self.logger.warning(
                    "EntityMergeService: failed to redirect %s -[%s]-> %s: %s",
                    new_subject, edge_type, new_object, exc,
                )
        if edges:
            await self.writer.invalidate(
                org_id,
                subject=duplicate if as_subject else None,
                obj=duplicate if not as_subject else None,
            )
        return moved


__all__ = ["EntityMergeService", "MergeError"]
