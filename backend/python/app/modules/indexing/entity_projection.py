"""Project taxonomy nodes of one org into the entity index, with membership
read from the graph. Shared by the background rebuild
(``entity_index_rebuild``) and taxonomy consolidation
(``app.modules.entity_resolution.consolidation``), so a node is written the
same way whichever of them touched it last.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.config.constants.arangodb import CollectionNames
from app.models.entities import EntityRecord, EntityType, EntityTypeCategory
from app.modules.entity_resolution.models import KINDS_BY_COLLECTION

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from logging import Logger

    from app.modules.transformers.entity_vectorstore import EntityVectorStore
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider


def taxonomy_entity_type(collection: str) -> tuple[EntityType, str | None]:
    """The entity type and subcategory level of points from ``collection``."""
    if collection == CollectionNames.DEPARTMENTS.value:
        return EntityType.DEPARTMENT, None
    kind = KINDS_BY_COLLECTION.get(collection)
    if kind is None:
        raise ValueError(f"{collection!r} is not a taxonomy collection")
    return kind.entity_type, kind.level


async def project_taxonomy_nodes(
    *,
    graph: IGraphDBProvider,
    store: EntityVectorStore,
    org_id: str,
    collection: str,
    rows: list[dict],
    logger: Logger,
    before_write: Callable[[], Awaitable[None]] | None = None,
) -> int:
    """Write points for ``rows`` (``{"_key"|"id", "name", "aliases"}``) that
    some record of ``org_id`` reaches, and delete the points of those none
    reaches. Membership comes from the graph and replaces what a point holds.

    ``before_write`` runs between the membership read and the first write;
    it may raise to abandon the page. Returns how many nodes failed.
    """
    entity_type, level = taxonomy_entity_type(collection)
    nodes = {
        key: row for row in rows
        if (key := _key_of(row)) and isinstance(row.get("name"), str) and row["name"].strip()
    }
    if not nodes:
        return 0
    try:
        membership = await graph.get_taxonomy_entity_membership(
            [{"id": key, "type": entity_type.value} for key in nodes], org_id,
        )
    except Exception:
        logger.warning(
            "entity_projection: membership lookup failed | org=%s collection=%s nodes=%d",
            org_id, collection, len(nodes), exc_info=True,
        )
        return len(nodes)
    if before_write is not None:
        await before_write()

    entities, unreached = [], []
    for key, row in nodes.items():
        reach = membership.get((entity_type.value, key)) or {}
        connector_ids = [c for c in reach.get("connectorIds") or [] if c]
        if not connector_ids:
            unreached.append(key)
            continue
        entities.append(EntityRecord(
            entity_id=key, entity_type=entity_type, name=row["name"], org_id=org_id,
            aliases=[str(a) for a in row.get("aliases") or [] if a], level=level,
            connector_ids=connector_ids,
            record_group_ids=[g for g in reach.get("recordGroupIds") or [] if g],
            type_category=EntityTypeCategory.GENERIC_SCHEMA_FREE,
        ))
    failed = 0
    if entities:
        # The graph is the source of membership, so it replaces what the
        # point holds (repairing lost updates). A record indexed between the
        # read and this write can lose its connector here until it is next
        # indexed; search only narrows, and every hit is re-checked.
        failed += (await store.upsert_entities_batch(entities, merge_membership=False)).failed
    if unreached:
        try:
            # Re-read: a record linked to the node since the first read has
            # had its point written by indexing, which must survive.
            again = await graph.get_taxonomy_entity_membership(
                [{"id": key, "type": entity_type.value} for key in unreached], org_id,
            )
            unreached = [
                key for key in unreached
                if not (again.get((entity_type.value, key)) or {}).get("connectorIds")
            ]
            await store.delete_entities(org_id, entity_type.value, unreached)
        except Exception:
            logger.warning(
                "entity_projection: delete of unreached nodes failed | org=%s collection=%s n=%d",
                org_id, collection, len(unreached), exc_info=True,
            )
            failed += len(unreached)
    return failed


def _key_of(row: dict) -> str | None:
    key = row.get("_key") or row.get("id")
    return key if isinstance(key, str) and key else None


__all__ = ["project_taxonomy_nodes", "taxonomy_entity_type"]
