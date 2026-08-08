"""Bi-temporal canonical-edge writer (KG Clean Rebuild plan, Phase 6).

Thin, provider-agnostic seam between the indexing pipeline and
``IGraphDBProvider``'s ``*_bitemporal_edge*`` methods (see
``app.services.graph_db.interface.graph_db_provider``, which already owns
the actual no-op / invalidate-and-replace write logic). This module exists
so callers (the resolution pipeline, ``CrossAppEntityLinker``) depend on one
small, typed surface — ``NodeRef`` + ``BitemporalGraphWriter`` — instead of
importing the graph provider directly and re-deriving the
``(id, collection)`` tuple convention at every call site.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, NamedTuple

if TYPE_CHECKING:
    import logging

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider


class NodeRef(NamedTuple):
    """A node identity, scoped to its collection — mirrors ArangoDB's
    ``{collection}/{key}`` addressing without hard-coding one DB's syntax."""
    node_id: str
    collection: str


class BitemporalGraphWriter:
    """Writes/reads canonical bi-temporal edges via ``IGraphDBProvider``.

    Every write is org-scoped; callers must always pass ``org_id`` rather
    than this class inferring or caching it, to avoid a cross-tenant leak
    creeping in through a stateful writer instance shared across requests.
    """

    def __init__(self, graph_provider: "IGraphDBProvider", logger: logging.Logger) -> None:
        self.graph_provider = graph_provider
        self.logger = logger

    async def write_edge(
        self,
        org_id: str,
        subject: NodeRef,
        obj: NodeRef,
        edge_type: str,
        attributes: dict[str, Any] | None = None,
        valid_at: int | None = None,
    ) -> dict[str, Any]:
        """Upsert a bi-temporal edge — no-ops if an identical current edge
        already exists, otherwise invalidates the old version (if any) and
        inserts a new current one. See
        ``IGraphDBProvider.upsert_bitemporal_edge`` for the full contract.
        """
        try:
            return await self.graph_provider.upsert_bitemporal_edge(
                org_id=org_id,
                from_id=subject.node_id, from_collection=subject.collection,
                to_id=obj.node_id, to_collection=obj.collection,
                edge_type=edge_type, attributes=attributes, valid_at=valid_at,
            )
        except Exception as exc:
            self.logger.error(
                "BitemporalGraphWriter.write_edge failed (%s -[%s]-> %s): %s",
                subject, edge_type, obj, exc,
            )
            raise

    async def invalidate(
        self,
        org_id: str,
        *,
        subject: NodeRef | None = None,
        obj: NodeRef | None = None,
        edge_type: str | None = None,
        invalid_at: int | None = None,
    ) -> int:
        """Close out currently-active edges without replacing them (e.g. a
        connector disconnect severing a cross-app link). Returns 0 and logs
        rather than invalidating an org's whole edge history if neither
        ``subject`` nor ``obj`` is given — see
        ``IGraphDBProvider.invalidate_bitemporal_edges``.
        """
        if subject is None and obj is None:
            self.logger.warning("BitemporalGraphWriter.invalidate: no subject/obj filter given, refusing")
            return 0
        return await self.graph_provider.invalidate_bitemporal_edges(
            org_id=org_id,
            from_id=subject.node_id if subject else None,
            from_collection=subject.collection if subject else None,
            to_id=obj.node_id if obj else None,
            to_collection=obj.collection if obj else None,
            edge_type=edge_type,
            invalid_at=invalid_at,
        )

    async def get_as_of(
        self,
        org_id: str,
        *,
        subject: NodeRef | None = None,
        obj: NodeRef | None = None,
        edge_type: str | None = None,
        as_of: int | None = None,
        include_history: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Read edges as of a point in time (``as_of=None`` means "now").
        See ``IGraphDBProvider.get_bitemporal_edges``."""
        return await self.graph_provider.get_bitemporal_edges(
            org_id=org_id,
            from_id=subject.node_id if subject else None,
            from_collection=subject.collection if subject else None,
            to_id=obj.node_id if obj else None,
            to_collection=obj.collection if obj else None,
            edge_type=edge_type,
            as_of=as_of,
            include_history=include_history,
            limit=limit,
            offset=offset,
        )


__all__ = ["BitemporalGraphWriter", "NodeRef"]
