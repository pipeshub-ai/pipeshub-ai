"""Reconcile one record's outbound edges against a desired set.

Named-entity mention writes use this. Existing edges are left untouched
unless ``update_existing`` is set, because an UPSERT of a shared edge takes a
write lock that collides under concurrent indexing.

Edges are written and deleted in target-id order. Every writer then locks
shared target nodes in the same order, so two records cannot deadlock on
them. A display name is no such order: one entity is "ACME" in one record
and "Acme" in another.
"""

from __future__ import annotations

from typing import Any

from app.config.constants.arangodb import CollectionNames
from app.utils.time_conversion import get_epoch_timestamp_in_ms


class EdgeReconciler:
    def __init__(self, logger) -> None:
        self.logger = logger

    async def reconcile(
        self,
        tx_store,
        *,
        record_id: str,
        record_from: str,
        edge_collection: str,
        new_tos: dict[str, str],
        label: str,
        edge_properties: dict[str, dict[str, Any]] | None = None,
        update_existing: bool = False,
    ) -> None:
        existing_edges = await tx_store.get_edges_from_node_with_target_name(
            record_from, edge_collection, raise_on_error=True
        )
        self.logger.debug("%d existing %s edges for record %s", len(existing_edges), label, record_id)
        existing_by_to: dict[str, dict] = {e["_to"]: e for e in existing_edges}
        properties = edge_properties or {}

        edges_to_create: list[dict] = []
        for to_full in sorted(new_tos):
            exists = to_full in existing_by_to
            if exists and not update_existing:
                continue
            to_collection, to_id = to_full.split("/", 1)
            edge = {
                "from_id": record_id,
                "from_collection": CollectionNames.RECORDS.value,
                "to_id": to_id,
                "to_collection": to_collection,
                "createdAtTimestamp": (
                    existing_by_to[to_full].get("createdAtTimestamp")
                    if exists
                    else get_epoch_timestamp_in_ms()
                ),
            }
            extra = properties.get(to_full) or {}
            for key, value in extra.items():
                if value is not None and value != "" and value != []:
                    edge[key] = value
            if exists:
                self.logger.debug("Updating %s edge: %s -> %s", label, record_id, to_full)
            else:
                self.logger.debug("Creating %s edge: %s -> %s", label, record_id, to_full)
            edges_to_create.append(edge)
        if edges_to_create:
            await tx_store.batch_create_edges(edges_to_create, edge_collection)

        stale_tos = sorted(to_full for to_full in existing_by_to if to_full not in new_tos)
        if not stale_tos:
            return
        from_collection, from_id = record_from.split("/", 1)
        stale_edges = []
        for to_full in stale_tos:
            to_collection, to_id = to_full.split("/", 1)
            stale_edges.append(
                {
                    "from_id": from_id,
                    "from_collection": from_collection,
                    "to_id": to_id,
                    "to_collection": to_collection,
                }
            )
        deleted_count = await tx_store.batch_delete_edges(stale_edges, edge_collection)
        for to_full in stale_tos:
            self.logger.info(f"🗑️ Deleted stale {label} edge: {record_id} -> {to_full}")
        self.logger.info(
            f"🧹 Deleted {deleted_count} stale {label} edges for record {record_id}"
        )
