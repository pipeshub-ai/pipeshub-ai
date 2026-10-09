"""An in-memory graph exposing both the provider methods the entity resolver
uses and the transaction-store methods ``GraphDBTransformer`` uses, so the
real resolver and transformer run without a database (the resolution
scenario tests, the resolution evaluation)."""

from __future__ import annotations

from typing import Any

from app.config.constants.arangodb import CollectionNames
from app.modules.entity_resolution.normalizer import normalize_name
from app.services.graph_db.taxonomy import (
    MAX_TAXONOMY_ALIASES,
    TAXONOMY_COLLECTIONS,
    TAXONOMY_EDGE_COLLECTIONS,
)

RECORDS = CollectionNames.RECORDS.value
DEPARTMENTS = CollectionNames.DEPARTMENTS.value


class FakeGraph:
    """In-memory graph exposing both the provider methods the resolver uses
    and the transaction-store methods ``GraphDBTransformer`` uses."""

    def __init__(self) -> None:
        self.nodes: dict[tuple[str, str], dict[str, Any]] = {}
        self.edges: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.records: dict[str, dict[str, Any]] = {}
        self.departments: dict[str, str] = {}
        self.calls: list[tuple[str, Any]] = []
        self.fail_find = False
        self.fail_node_lookup = False
        self.node_lookup_kwargs: list[dict[str, Any]] = []
        # Raised by the next filter / edge reads; like both providers, a
        # failed read is [] unless the caller passes raise_on_error.
        self.fail_filter_reads: list[Exception] = []
        self.fail_edge_reads: list[Exception] = []

    # ---- setup helpers ----
    def add_record(self, key: str, org_id: str, connector_id: str = "conn-1",
                   record_group_id: str = "rg-1") -> None:
        self.records[key] = {
            "_key": key, "orgId": org_id, "connectorId": connector_id,
            "recordGroupId": record_group_id,
        }

    def add_department(self, name: str, key: str | None = None) -> None:
        self.departments[name] = key or f"dept-{normalize_name(name)}"

    def add_legacy_node(self, collection: str, key: str, name: str) -> None:
        self.nodes[(collection, key)] = {"name": name}

    # ---- inspection helpers ----
    def nodes_in(self, collection: str) -> list[dict[str, Any]]:
        return [dict(n, key=k) for (c, k), n in self.nodes.items() if c == collection]

    def node(self, collection: str, key: str) -> dict[str, Any]:
        return self.nodes[(collection, key)]

    def edges_from(self, record_key: str, edge_collection: str) -> list[dict[str, Any]]:
        return [
            e for (c, f, _t), e in self.edges.items()
            if c == edge_collection and f == f"{RECORDS}/{record_key}"
        ]

    def edges_to(self, target_full: str) -> list[dict[str, Any]]:
        return [e for (_c, _f, t), e in self.edges.items() if t == target_full]

    # ---- provider-level (resolver) ----
    async def find_taxonomy_nodes(self, collection, org_id, normalized_names, transaction=None) -> list[dict[str, Any]]:
        self.calls.append(("find_taxonomy_nodes", (collection, org_id, list(normalized_names))))
        if self.fail_find:
            raise RuntimeError("graph down")
        wanted = set(normalized_names)
        return [
            {
                "id": key,
                "name": node["name"],
                "normalizedName": node.get("normalizedName"),
                "aliases": list(node.get("aliases") or []),
                "normalizedAliases": list(node.get("normalizedAliases") or []),
            }
            for (coll, key), node in self.nodes.items()
            if coll == collection
            and node.get("orgId") == org_id
            and not node.get("mergedInto")
            and (
                node.get("normalizedName") in wanted
                or wanted & set(node.get("normalizedAliases") or [])
            )
        ]

    async def get_nodes_by_field_in(
        self, collection, field_name, field_values, return_fields=None, transaction=None,
        *, raise_on_error=False,
    ) -> list[dict[str, Any]]:
        self.calls.append(("get_nodes_by_field_in", (collection, field_name, list(field_values))))
        self.node_lookup_kwargs.append({"raise_on_error": raise_on_error})
        if self.fail_node_lookup:
            # Like both real providers: swallowed unless the caller asks.
            if raise_on_error:
                raise RuntimeError("graph down")
            return []
        assert field_name == "id"
        wanted = set(field_values)
        return [
            {"id": key, **node}
            for (coll, key), node in self.nodes.items()
            if coll == collection and key in wanted
        ]

    async def create_taxonomy_node_if_absent(self, collection, node, transaction=None) -> None:
        self.calls.append(("create_taxonomy_node_if_absent", (collection, dict(node))))
        key = (collection, node["id"])
        if key in self.nodes:
            return
        stored = {k: v for k, v in node.items() if k not in ("id", "aliases")}
        stored.setdefault("aliases", [])
        self.nodes[key] = stored

    async def ensure_taxonomy_hierarchy_edge(self, child_collection, child_key, parent_key) -> None:
        from app.services.graph_db.taxonomy import CATEGORY_HIERARCHY_PARENTS

        parent_collection = CATEGORY_HIERARCHY_PARENTS[child_collection]
        edge = (
            CollectionNames.INTER_CATEGORY_RELATIONS.value,
            f"{child_collection}/{child_key}",
            f"{parent_collection}/{parent_key}",
        )
        self.edges.setdefault(edge, {"from_id": child_key, "to_id": parent_key})

    async def add_taxonomy_aliases(
        self, collection, key, aliases, normalized_aliases, *, org_id, max_aliases=MAX_TAXONOMY_ALIASES,
        transaction=None,
    ) -> None:
        self.calls.append(("add_taxonomy_aliases", (collection, key, list(aliases), list(normalized_aliases))))
        node = self.nodes.get((collection, key))
        # Like both providers: only the writing org's node takes aliases.
        if node is None or node.get("orgId") != org_id:
            return
        current = list(node.get("aliases") or [])
        current_normalized = list(node.get("normalizedAliases") or [])
        for alias, normalized in zip(aliases, normalized_aliases):
            if alias and normalized not in current_normalized:
                current.append(alias)
                current_normalized.append(normalized)
        node["aliases"] = current[:max_aliases]
        node["normalizedAliases"] = current_normalized[:max_aliases]

    async def get_record_taxonomy_links(self, record_keys, transaction=None) -> list[dict[str, Any]]:
        """Rows shaped as both providers return them: every category,
        subcategory, topic and language edge of the given records."""
        wanted = set(record_keys)
        rows: list[dict[str, Any]] = []
        for (edge_collection, frm, to), edge in self.edges.items():
            if edge_collection not in TAXONOMY_EDGE_COLLECTIONS.values():
                continue
            from_collection, record_key = frm.split("/", 1)
            to_collection, entity_key = to.split("/", 1)
            if from_collection != RECORDS or record_key not in wanted:
                continue
            if to_collection not in TAXONOMY_COLLECTIONS:
                continue
            node = self.nodes.get((to_collection, entity_key))
            if node is None:
                continue
            rows.append({
                "recordId": record_key,
                "collection": to_collection,
                "entityId": entity_key,
                "name": node.get("name"),
                "canonical": node.get("normalizedName") is not None,
                "extractedName": edge.get("extractedName"),
                "migrated": edge.get("migratedFrom") is not None,
            })
        return rows

    # ---- transaction-store level (GraphDBTransformer) ----
    async def get_record_by_key(self, key, *, raise_on_error: bool = False) -> dict[str, Any] | None:
        return self.records.get(key)

    async def get_nodes_by_filters(self, collection, filters, return_fields=None, *,
                                   raise_on_error=False) -> list[dict[str, Any]]:
        self.calls.append(("get_nodes_by_filters", (collection, dict(filters))))
        if self.fail_filter_reads:
            error = self.fail_filter_reads.pop(0)
            if raise_on_error:
                raise error
            return []
        if collection == DEPARTMENTS:
            name = filters.get("departmentName")
            return [{"_key": self.departments[name]}] if name in self.departments else []
        return [
            {"_key": key, **node}
            for (coll, key), node in self.nodes.items()
            if coll == collection and all(node.get(f) == v for f, v in filters.items())
        ]

    async def batch_upsert_nodes(self, nodes, collection) -> bool:
        self.calls.append(("batch_upsert_nodes", (collection, [dict(n) for n in nodes])))
        for node in nodes:
            stored = {k: v for k, v in node.items() if k != "id"}
            self.nodes[(collection, node["id"])] = stored
        return True

    async def batch_update_nodes(self, nodes, collection) -> bool:
        # Both providers merge each patch into an existing node only, and
        # report False when any target is missing.
        all_found = True
        for node in nodes:
            key = node.get("id", node.get("_key"))
            target = self.records.get(key) if collection == RECORDS else self.nodes.get((collection, key))
            if target is None:
                all_found = False
                continue
            target.update({k: v for k, v in node.items() if k not in ("id", "_key")})
        return all_found

    def is_write_conflict(self, error: BaseException) -> bool:
        return "write conflict" in str(error)

    async def get_edge(self, from_key, from_collection, to_key, to_collection, edge_collection) -> dict[str, Any] | None:
        return self.edges.get(
            (edge_collection, f"{from_collection}/{from_key}", f"{to_collection}/{to_key}")
        )

    async def get_edges_from_node_with_target_name(self, record_from, edge_collection, *,
                                                   raise_on_error=False) -> list[dict[str, Any]]:
        if self.fail_edge_reads:
            error = self.fail_edge_reads.pop(0)
            if raise_on_error:
                raise error
            return []
        out = []
        for (coll, frm, to), edge in self.edges.items():
            if coll == edge_collection and frm == record_from:
                to_coll, to_key = to.split("/", 1)
                node = self.nodes.get((to_coll, to_key), {})
                out.append({"_to": to, "name": node.get("name", to_key), **edge})
        return out

    async def batch_create_edges(self, edges, edge_collection) -> bool:
        for edge in edges:
            frm = f"{edge['from_collection']}/{edge['from_id']}"
            to = f"{edge['to_collection']}/{edge['to_id']}"
            self.edges[(edge_collection, frm, to)] = dict(edge)
        return True

    async def batch_delete_edges(self, edges, edge_collection) -> int:
        deleted = 0
        for edge in edges:
            frm = f"{edge['from_collection']}/{edge['from_id']}"
            to = f"{edge['to_collection']}/{edge['to_id']}"
            if self.edges.pop((edge_collection, frm, to), None) is not None:
                deleted += 1
        return deleted
