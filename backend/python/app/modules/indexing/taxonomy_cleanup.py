"""Cleanup of taxonomy entity points once a record stops linking to them."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.exceptions.indexing_exceptions import IndexingError

if TYPE_CHECKING:
    from app.modules.transformers.entity_vectorstore import EntityVectorStore
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider


async def delete_unreached_taxonomy_points(
    store: EntityVectorStore, graph: IGraphDBProvider, org_id: str, record_id: str, refs: object,
) -> None:
    """Delete the points of the taxonomy entities in ``refs`` that no live record reaches any more.

    Raises so the caller's retry runs again, then dead-letters; every step is idempotent, so a retry repeats them harmlessly.
    """
    if not refs or not isinstance(refs, list):
        return
    try:
        await store.delete_unreached_entities(
            org_id,
            refs,
            lambda wanted: graph.get_unreached_taxonomy_entities(wanted, org_id),
        )
    except Exception as exc:
        raise IndexingError(
            f"Entity cleanup for record {record_id} did not complete",
            details={"org_id": org_id, "record_id": record_id},
        ) from exc
