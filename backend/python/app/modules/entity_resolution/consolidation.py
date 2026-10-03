"""Taxonomy consolidation: reversible merges of duplicate nodes within an org
(KG-33), and moving an org's records off legacy nodes onto its canonical
per-org nodes (B7). Run from ``app/scripts/kg_taxonomy.py``, never inline.

A merge is a recorded redirect, not a delete:
- the loser keeps its data and gets ``mergedInto`` and ``mergedAt``;
- its record edges move to the winner, keeping ``extractedName`` and
  gaining ``mergedFrom``;
- the winner learns the loser's spellings as aliases.

``unmerge`` moves the marked edges back. Lookups skip a node with
``mergedInto``, and the resolver follows the redirect for a name whose
deterministic key is a merged node, so a later record does not re-link to it.

Merges record ``mergedFrom`` on edges and migrations ``migratedFrom``, so
undoing one never hides the other's edges. An edge keeps the ``mergedFrom``
of its first move, and a merge re-points
nodes that redirected to the loser at the winner. So chained merges stay
undoable one node at a time, and every undo follows redirects to the node
that holds the edges now.

Only nodes whose names share a spelling key are proposed for merging: they
differ in case, spacing or punctuation, never in words (see
``normalizer.spelling_key``). Anything looser needs a human or a model and
is out of scope here.

Legacy nodes (no ``orgId``) are shared by every org, so migration moves one
org's edges at a time onto that org's canonical node and leaves the legacy
node, and other orgs' edges, alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.modules.entity_resolution.keys import taxonomy_node_key
from app.modules.entity_resolution.normalizer import (
    display_form,
    is_acceptable_name,
    normalize_name,
    spelling_key,
)
from app.modules.indexing.entity_projection import (
    project_taxonomy_nodes,
    taxonomy_entity_type,
)
from app.services.graph_db.taxonomy import (
    MAX_MERGE_REDIRECT_HOPS,
    MERGED_INTO_FIELD,
    is_taxonomy_collection,
)
from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    from collections.abc import Callable
    from logging import Logger

    from app.modules.transformers.entity_vectorstore import EntityVectorStore
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

MERGED_AT_FIELD = "mergedAt"
# Edge provenance of a legacy migration; merges use mergedFrom.
_MIGRATED = "migratedFrom"
_NODE_FIELDS = ["id", "name", "aliases", "orgId", "normalizedName", "createdAtTimestamp", MERGED_INTO_FIELD]
_PAGE_SIZE = 500
_LEGACY_PAGE = 500



@dataclass(frozen=True)
class TaxonomyNode:
    collection: str
    key: str
    name: str
    org_id: str | None
    aliases: tuple[str, ...] = ()
    created_at: int = 0
    merged_into: str | None = None


@dataclass(frozen=True)
class DuplicateGroup:
    collection: str
    org_id: str
    winner: TaxonomyNode
    losers: tuple[TaxonomyNode, ...]


@dataclass(frozen=True)
class LegacyNode:
    key: str
    name: str
    records: int


@dataclass(frozen=True)
class MergeResult:
    edges_moved: int
    dry_run: bool
    index_refreshed: bool = True


@dataclass(frozen=True)
class MigrationResult:
    target_key: str | None
    edges_moved: int
    dry_run: bool
    skipped_reason: str | None = None
    index_refreshed: bool = True


class TaxonomyConsolidator:
    def __init__(
        self,
        *,
        graph_provider: IGraphDBProvider,
        entity_store: EntityVectorStore | None,
        logger: Logger,
        page_size: int = _PAGE_SIZE,
        now_ms: Callable[[], int] = get_epoch_timestamp_in_ms,
    ) -> None:
        self.graph = graph_provider
        self.store = entity_store
        self.logger = logger
        self.page_size = max(1, page_size)
        self.now_ms = now_ms

    # ------------------------------------------------------------------
    # Duplicates
    # ------------------------------------------------------------------

    async def duplicate_groups(self, collection: str, org_id: str) -> list[DuplicateGroup]:
        """Groups of the org's live canonical nodes whose names share a
        spelling key. The oldest node wins; ties go to the smaller key."""
        _check_collection(collection)
        by_key: dict[str, list[TaxonomyNode]] = {}
        after: str | None = None
        while True:
            rows = await self.graph.page_entity_index_source(collection, org_id, after, self.page_size)
            for row in rows:
                node = _node(collection, row)
                if node is not None and (spelled := spelling_key(node.name)):
                    by_key.setdefault(spelled, []).append(node)
            if len(rows) < self.page_size:
                break
            after = rows[-1].get("_key") or rows[-1].get("id")
        groups = []
        for nodes in by_key.values():
            if len(nodes) < 2:
                continue
            ordered = sorted(nodes, key=lambda n: (n.created_at, n.key))
            groups.append(DuplicateGroup(collection, org_id, ordered[0], tuple(ordered[1:])))
        return sorted(groups, key=lambda g: g.winner.key)

    async def merge(
        self, collection: str, org_id: str, winner_key: str, loser_key: str, *, dry_run: bool = True,
    ) -> MergeResult:
        """Merge ``loser_key`` into ``winner_key``. Re-running on a loser
        already merged into the same winner moves edges linked since."""
        _check_collection(collection)
        if winner_key == loser_key:
            raise ValueError("a node cannot be merged into itself")
        nodes = await self._nodes(collection, [winner_key, loser_key])
        winner, loser = nodes.get(winner_key), nodes.get(loser_key)
        for node, role in ((winner, "winner"), (loser, "loser")):
            if node is None:
                raise ValueError(f"{role} {collection}/{winner_key if role == 'winner' else loser_key} not found")
            if node.org_id != org_id:
                raise ValueError(f"{role} {collection}/{node.key} is not a node of org {org_id}")
        assert winner is not None and loser is not None
        if winner.merged_into:
            raise ValueError(f"winner {collection}/{winner.key} was merged into {winner.merged_into}")
        if loser.merged_into and (await self._final(collection, org_id, loser.merged_into)).key != winner.key:
            raise ValueError(f"loser {collection}/{loser.key} was merged into {loser.merged_into}")

        moved = await self.graph.move_taxonomy_edges(
            collection, loser.key, winner.key, org_id, set_merged_from=loser.key, dry_run=dry_run,
        )
        self.logger.info(
            "kg_taxonomy: merge %s | org=%s winner=%s loser=%s edges=%d",
            "planned" if dry_run else "applied", org_id, winner.key, loser.key, moved,
        )
        if dry_run:
            return MergeResult(moved, dry_run=True)

        spellings = [loser.name, *loser.aliases]
        await self.graph.add_taxonomy_aliases(
            collection, winner.key, spellings, [normalize_name(s) for s in spellings], org_id=org_id,
        )
        if loser.merged_into is None:
            # Marked only after the edges moved: a crash in between leaves an
            # unmarked loser that a re-run finishes.
            await self.graph.update_node(loser.key, collection, {
                MERGED_INTO_FIELD: winner.key, MERGED_AT_FIELD: self.now_ms(),
            })
            await self._flatten(collection, loser.key, winner.key)
            # Records linked to the loser while the edges moved.
            moved += await self.graph.move_taxonomy_edges(
                collection, loser.key, winner.key, org_id, set_merged_from=loser.key,
            )
        refreshed = await self._refresh_index(collection, org_id, keep=[winner.key], drop=[loser.key])
        return MergeResult(moved, dry_run=False, index_refreshed=refreshed)

    async def unmerge(self, collection: str, org_id: str, loser_key: str, *, dry_run: bool = True) -> int:
        """Undo ``merge``: move the edges the merge moved back to the loser
        and clear its redirect. Aliases the winner learned are kept."""
        _check_collection(collection)
        loser = (await self._nodes(collection, [loser_key])).get(loser_key)
        if loser is None or loser.org_id != org_id or not loser.merged_into:
            raise ValueError(f"{collection}/{loser_key} is not a merged node of org {org_id}")
        winner_key = (await self._final(collection, org_id, loser.merged_into)).key
        restored = await self.graph.move_taxonomy_edges(
            collection, winner_key, loser.key, org_id,
            set_merged_from=None, only_merged_from=loser.key, dry_run=dry_run,
        )
        self.logger.info(
            "kg_taxonomy: unmerge %s | org=%s winner=%s loser=%s edges=%d",
            "planned" if dry_run else "applied", org_id, winner_key, loser.key, restored,
        )
        if dry_run:
            return restored
        await self.graph.update_node(loser.key, collection, {MERGED_INTO_FIELD: None, MERGED_AT_FIELD: None})
        await self._refresh_index(collection, org_id, keep=[winner_key, loser.key], drop=[])
        return restored

    # ------------------------------------------------------------------
    # Legacy nodes
    # ------------------------------------------------------------------

    async def legacy_nodes(self, collection: str, org_id: str) -> list[LegacyNode]:
        """Legacy nodes (no ``orgId``) that records of ``org_id`` link to,
        paged through in key order."""
        _check_collection(collection)
        nodes: list[LegacyNode] = []
        after: str | None = None
        while True:
            rows = await self.graph.find_legacy_taxonomy_nodes(
                collection, org_id, _LEGACY_PAGE, after_key=after,
            )
            for row in rows:
                key = row.get("_key") or row.get("id")
                if key:
                    nodes.append(LegacyNode(str(key), str(row.get("name") or ""), int(row.get("records") or 0)))
            if len(rows) < _LEGACY_PAGE:
                return nodes
            after = nodes[-1].key

    async def migrate_legacy(
        self, collection: str, org_id: str, legacy_key: str, *, dry_run: bool = True,
    ) -> MigrationResult:
        """Move the org's edges from a legacy node onto the org's canonical
        node for the same name, creating it if absent and following a merge
        redirect. Other orgs' edges and the legacy node are left alone."""
        _check_collection(collection)
        legacy = (await self._nodes(collection, [legacy_key])).get(legacy_key)
        if legacy is None:
            raise ValueError(f"{collection}/{legacy_key} not found")
        if legacy.org_id is not None:
            raise ValueError(f"{collection}/{legacy_key} belongs to org {legacy.org_id}; not a legacy node")
        normalized = normalize_name(legacy.name)
        if not is_acceptable_name(normalized):
            self.logger.info(
                "kg_taxonomy: legacy node skipped | org=%s node=%s reason=unusable name", org_id, legacy_key,
            )
            return MigrationResult(None, 0, dry_run, skipped_reason="name is not a usable taxonomy label")

        target_key, target_exists = await self._migration_target(collection, org_id, normalized)
        if dry_run:
            moved = await self.graph.move_taxonomy_edges(
                collection, legacy_key, target_key, org_id, set_merged_from=legacy_key, provenance=_MIGRATED, dry_run=True,
            )
            return MigrationResult(target_key, moved, dry_run=True)

        if not target_exists:
            await self.graph.create_taxonomy_node_if_absent(collection, {
                "id": target_key,
                "name": display_form(legacy.name),
                "normalizedName": normalized,
                "orgId": org_id,
                "createdAtTimestamp": self.now_ms(),
            })
        moved = await self.graph.move_taxonomy_edges(
            collection, legacy_key, target_key, org_id, set_merged_from=legacy_key, provenance=_MIGRATED,
        )
        self.logger.info(
            "kg_taxonomy: legacy migrated | org=%s legacy=%s target=%s edges=%d",
            org_id, legacy_key, target_key, moved,
        )
        refreshed = await self._refresh_index(collection, org_id, keep=[target_key], drop=[legacy_key])
        return MigrationResult(target_key, moved, dry_run=False, index_refreshed=refreshed)

    async def unmigrate_legacy(
        self, collection: str, org_id: str, legacy_key: str, target_key: str, *, dry_run: bool = True,
    ) -> int:
        """Undo ``migrate_legacy`` for one org, wherever later merges moved
        the edges."""
        _check_collection(collection)
        target_key = (await self._final(collection, org_id, target_key)).key
        restored = await self.graph.move_taxonomy_edges(
            collection, target_key, legacy_key, org_id,
            set_merged_from=None, only_merged_from=legacy_key, provenance=_MIGRATED, dry_run=dry_run,
        )
        if not dry_run:
            await self._refresh_index(collection, org_id, keep=[target_key], drop=[])
        return restored

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _nodes(self, collection: str, keys: list[str]) -> dict[str, TaxonomyNode]:
        rows = await self.graph.get_nodes_by_field_in(
            collection, "id", list(dict.fromkeys(keys)), return_fields=_NODE_FIELDS, raise_on_error=True,
        )
        out = {}
        for row in rows or []:
            node = _node(collection, row)
            if node is not None:
                out[node.key] = node
        return out

    async def _final(self, collection: str, org_id: str, key: str) -> TaxonomyNode:
        """The node ``key`` redirects to after every merge, ``key`` itself if
        it was never merged."""
        seen: list[str] = []
        while True:
            node = (await self._nodes(collection, [key])).get(key)
            if node is None or node.org_id != org_id:
                raise ValueError(f"{collection}/{key} is not a node of org {org_id}")
            if not node.merged_into:
                return node
            seen.append(key)
            if node.merged_into in seen or len(seen) > MAX_MERGE_REDIRECT_HOPS:
                raise ValueError(f"redirect cycle or overlong chain at {collection}/{key}: {seen}")
            key = node.merged_into

    async def _flatten(self, collection: str, loser_key: str, winner_key: str) -> None:
        """Point nodes that redirected to the loser at the winner, so no
        redirect leads to a node that is itself merged."""
        rows = await self.graph.get_nodes_by_field_in(
            collection, MERGED_INTO_FIELD, [loser_key], return_fields=["id"], raise_on_error=True,
        )
        for row in rows or []:
            key = row.get("id") or row.get("_key")
            if key and key != winner_key:
                await self.graph.update_node(key, collection, {MERGED_INTO_FIELD: winner_key})

    async def _migration_target(self, collection: str, org_id: str, normalized: str) -> tuple[str, bool]:
        """Where an org's edges from a legacy node with this normalized name
        go: the org's live node for the name or one of its aliases, else the
        node at the deterministic key (following a merge redirect), else a
        new node at that key. Returns the key and whether it exists."""
        found = await self.graph.find_taxonomy_nodes(collection, org_id, [normalized])
        live = sorted(str(r.get("id")) for r in found or [] if r.get("id"))
        if live:
            # A name and an alias can match two nodes; prefer the exact name.
            exact = [str(r["id"]) for r in found if r.get("normalizedName") == normalized]
            return (exact or live)[0], True
        key = taxonomy_node_key(org_id, collection, normalized)
        node = (await self._nodes(collection, [key])).get(key)
        if node is None:
            return key, False
        return (await self._final(collection, org_id, key)).key, True

    async def _refresh_index(self, collection: str, org_id: str, *, keep: list[str], drop: list[str]) -> bool:
        """Re-project ``keep`` from the graph and delete the org's points of
        ``drop``. The graph change already happened, so a failure is logged
        and reported, not raised; the rebuild sweep repairs it later."""
        if self.store is None:
            return True
        entity_type, _ = taxonomy_entity_type(collection)
        try:
            if drop:
                await self.store.delete_entities(org_id, entity_type.value, drop)
            nodes = await self._nodes(collection, keep)
            failed = await project_taxonomy_nodes(
                graph=self.graph, store=self.store, org_id=org_id, collection=collection,
                rows=[{"_key": n.key, "name": n.name, "aliases": list(n.aliases)} for n in nodes.values()],
                logger=self.logger,
            )
            return not failed
        except Exception:
            self.logger.warning(
                "kg_taxonomy: entity index not refreshed | org=%s collection=%s keep=%s drop=%s",
                org_id, collection, keep, drop, exc_info=True,
            )
            return False


def _check_collection(collection: str) -> None:
    if not is_taxonomy_collection(collection):
        raise ValueError(f"{collection!r} is not a taxonomy collection")


def _node(collection: str, row: dict[str, Any]) -> TaxonomyNode | None:
    key = row.get("_key") or row.get("id")
    name = row.get("name")
    if not isinstance(key, str) or not key or not isinstance(name, str) or not name.strip():
        return None
    try:
        created = int(row.get("createdAtTimestamp") or 0)
    except (TypeError, ValueError):
        created = 0
    return TaxonomyNode(
        collection=collection,
        key=key,
        name=name,
        org_id=row.get("orgId") or None,
        aliases=tuple(str(a) for a in row.get("aliases") or [] if a),
        created_at=created,
        merged_into=row.get(MERGED_INTO_FIELD) or None,
    )


__all__ = [
    "MERGED_INTO_FIELD",
    "DuplicateGroup",
    "LegacyNode",
    "MergeResult",
    "MigrationResult",
    "TaxonomyConsolidator",
    "TaxonomyNode",
]
