"""An in-memory stand-in for the graph provider.

Implements only what block projection and edge resolution actually call, so the
resolution ladder can be exercised without a database.
"""
import pytest

from app.config.constants.arangodb import CollectionNames
from app.modules.code_graph.block_projection import (
    BlockProjectionContext,
    write_code_file_blocks_to_graph,
)
from app.modules.parsers.code_parser import CodeFileParser

ORG_ID = "org1"
GROUP_ID = "repo1"


class FakeGraphProvider:
    def __init__(self) -> None:
        self.blocks: dict[str, dict] = {}
        self.edges: list[dict] = []
        # recordId -> file path. The real graph keeps this on `codeFiles`, one
        # row per file, rather than on every block.
        self.code_files: dict[str, str] = {}

    async def get_file_paths_for_records(self, org_id, record_ids, transaction=None) -> dict:
        wanted = set(record_ids)
        return {
            record_id: path
            for record_id, path in self.code_files.items()
            if record_id in wanted
        }

    async def get_documents_paginated(self, collection, skip=0, limit=50, filters=None,
                                      sort_field=None, transaction=None, raise_on_error=False):
        if collection != CollectionNames.BLOCKS.value:
            return []
        rows = [
            doc for doc in self.blocks.values()
            if all(doc.get(field) == value for field, value in (filters or {}).items())
        ]
        rows.sort(key=lambda d: d.get("_key") or "")
        return rows[skip: skip + limit]

    async def get_edges_by_target_keys(
        self, target_keys, edge_collection, filters=None, return_field="_from",
        transaction=None,
    ):
        targets = set(target_keys)
        return sorted({
            edge[return_field]
            for edge in self.edges
            if edge.get("_to") in targets
            and all(
                edge.get(field) == value
                for field, value in (filters or {}).items()
            )
        })

    async def delete_edges_by_source_keys(
        self, source_keys, edge_collection, filters=None, transaction=None,
    ):
        sources = set(source_keys)
        keep, removed = [], 0
        for edge in self.edges:
            matches = (
                edge.get("_from") in sources
                and all(
                    edge.get(field) == value
                    for field, value in (filters or {}).items()
                )
            )
            if matches:
                removed += 1
            else:
                keep.append(edge)
        self.edges = keep
        return removed

    async def delete_edges_touching_nodes(
        self, node_keys, node_collection, edge_collection, transaction=None,
    ):
        node_ids = {f"{node_collection}/{key}" for key in node_keys}
        original_count = len(self.edges)
        self.edges = [
            edge for edge in self.edges
            if edge.get("_from") not in node_ids
            and edge.get("_to") not in node_ids
        ]
        return original_count - len(self.edges)

    async def get_nodes_by_filters(self, collection, filters, return_fields=None, transaction=None):
        return [
            {"_key": key} for key, doc in self.blocks.items()
            if all(doc.get(field) == value for field, value in filters.items())
        ]

    async def batch_upsert_nodes(self, nodes, collection, transaction=None):
        for node in nodes:
            doc = dict(node)
            doc["_key"] = doc.pop("id")
            self.blocks[doc["_key"]] = doc
        return True

    async def delete_nodes(self, keys, collection, transaction=None):
        for key in keys:
            self.blocks.pop(key, None)
        return True

    async def batch_upsert_record_relations(self, edges, transaction=None):
        seen = {(e["_from"], e["_to"], e["relationshipType"]) for e in self.edges}
        for edge in edges:
            key = (edge["_from"], edge["_to"], edge["relationshipType"])
            if key not in seen:
                self.edges.append(edge)
                seen.add(key)
        return True

    # -- assertions helpers ---------------------------------------------

    def code_edges(self, relation=None):
        return [
            e for e in self.edges
            if e.get("source") == "code_graph"
            and (relation is None or e["relationshipType"] == relation)
        ]

    def edge_targets(self, relation):
        out = []
        for edge in self.code_edges(relation):
            _, _, ident = edge["_to"].partition("/")
            doc = self.blocks.get(ident)
            out.append(doc.get("qualifiedName") if doc else edge["_to"])
        return sorted(filter(None, out))


@pytest.fixture
def graph():
    return FakeGraphProvider()


@pytest.fixture
def index_file(graph):
    parser = CodeFileParser()

    async def _index(record_id, file_path, source, language, name=None):
        container = parser.parse_to_blocks(
            source, name or file_path.rsplit("/", 1)[-1], file_path, language
        )
        graph.code_files[record_id] = file_path
        ctx = BlockProjectionContext(
            org_id=ORG_ID,
            record_id=record_id,
            record_group_id=GROUP_ID,
            connector_id="conn1",
            language=language,
        )
        return await write_code_file_blocks_to_graph(
            graph_provider=graph, context=ctx, block_containers=container
        )

    return _index
