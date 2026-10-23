"""An in-memory stand-in for the graph provider.

Implements only what block projection and edge resolution actually call, so the
resolution ladder can be exercised without a database.
"""
from collections.abc import Awaitable, Callable

import pytest

from app.config.constants.arangodb import CollectionNames
from app.modules.code_graph.block_projection import (
    BlockProjectionContext,
    write_code_file_blocks_to_graph,
)
from app.modules.code_graph.facts_source import RecordFacts, facts_from_blob_record
from app.modules.parsers.code_parser import CodeFileParser
from app.services.graph_db.interface.graph_db_provider import CodeFileInfo

ORG_ID = "org1"
GROUP_ID = "repo1"


class FakeGraphProvider:
    def __init__(self) -> None:
        self.blocks: dict[str, dict] = {}
        self.edges: list[dict] = []
        # recordId -> file path. The real graph keeps this on `codeFiles`, one
        # row per file, rather than on every block.
        self.code_files: dict[str, str] = {}
        # recordId -> referenced names. Also on codeFiles in production.
        self.referenced_names: dict[str, tuple[str, ...]] = {}
        # recordId -> {"changedNames", "changedNamesAt"}, also on codeFiles.
        self.changed_names: dict[str, dict] = {}
        # recordId -> the parsed record as the indexing pipeline uploads it.
        # Doubles as the `CodeFactsSource` (see `load`), so a test's blob is
        # the same parse that projected the blocks, as in production.
        self.blobs: dict[str, dict] = {}

    async def load(self, org_id, record_ids) -> dict[str, RecordFacts]:
        return {
            record_id: facts_from_blob_record(record_id, self.blobs[record_id])
            for record_id in record_ids
            if record_id in self.blobs
        }

    async def get_file_paths_for_records(self, org_id, record_ids, transaction=None,
                                         raise_on_error=False) -> dict:
        wanted = set(record_ids)
        return {
            record_id: CodeFileInfo(
                file_path=path,
                referenced_names=self.referenced_names.get(record_id, ()),
                changed_names=tuple(self.changed_names.get(record_id, {}).get("changedNames", ())),
            )
            for record_id, path in self.code_files.items()
            if record_id in wanted
        }

    async def get_document(self, key, collection, transaction=None, *, raise_on_error=False) -> dict | None:
        if collection == CollectionNames.CODE_FILES.value and key in self.code_files:
            return {"_key": key, "filePath": self.code_files[key], **self.changed_names.get(key, {})}
        return None

    async def update_node(self, key, collection, updates, transaction=None) -> bool:
        if collection == CollectionNames.CODE_FILES.value:
            if "referencedNames" in updates:
                self.referenced_names[key] = tuple(updates["referencedNames"] or ())
            if "changedNames" in updates:
                self.changed_names[key] = {
                    "changedNames": list(updates["changedNames"]),
                    "changedNamesAt": updates.get("changedNamesAt"),
                }
        return True

    async def get_documents_paginated(self, collection, skip=0, limit=50, filters=None,
                                      sort_field=None, transaction=None, raise_on_error=False,
                                      after_key=None, return_fields=None) -> list[dict]:
        if collection != CollectionNames.BLOCKS.value:
            return []
        rows = [
            doc for doc in self.blocks.values()
            if all(doc.get(field) == value for field, value in (filters or {}).items())
            and (after_key is None or (doc.get("_key") or "") > after_key)
        ]
        rows.sort(key=lambda d: d.get("_key") or "")
        page = rows[skip: skip + limit]
        if return_fields:
            return [{field: doc.get(field) for field in return_fields} for doc in page]
        return page

    async def get_edges_by_target_keys(
        self, target_keys, edge_collection, filters=None, return_field="_from",
        transaction=None, raise_on_error=False,
    ) -> list:
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
        self, source_keys, edge_collection, filters=None, transaction=None, exclude=None,
    ) -> int:
        sources = set(source_keys)
        keep, removed = [], 0
        for edge in self.edges:
            matches = (
                edge.get("_from") in sources
                and all(
                    edge.get(field) == value
                    for field, value in (filters or {}).items()
                )
                and not any(edge.get(field) == value for field, value in (exclude or {}).items())
            )
            if matches:
                removed += 1
            else:
                keep.append(edge)
        self.edges = keep
        return removed

    async def delete_edges_touching_nodes(
        self, node_keys, node_collection, edge_collection, transaction=None,
    ) -> int:
        node_ids = {f"{node_collection}/{key}" for key in node_keys}
        original_count = len(self.edges)
        self.edges = [
            edge for edge in self.edges
            if edge.get("_from") not in node_ids
            and edge.get("_to") not in node_ids
        ]
        return original_count - len(self.edges)

    async def get_nodes_by_filters(self, collection, filters, return_fields=None, transaction=None,
                                   *, raise_on_error=False) -> list[dict]:
        if collection == CollectionNames.RECORDS.value:
            # The repo's code files, empty ones included, as the builder lists them.
            return [{"_key": record_id} for record_id in self.code_files]
        return [
            {"_key": key, "name": doc.get("name"), "kind": doc.get("kind")}
            for key, doc in self.blocks.items()
            if all(doc.get(field) == value for field, value in filters.items())
        ]

    async def count_nodes_by_filters(self, collection, filters=None, in_filters=None,
                                     transaction=None) -> int:
        return sum(
            1 for doc in self.blocks.values()
            if all(doc.get(field) == value for field, value in (filters or {}).items())
        )

    async def delete_blocks_for_records(self, record_ids, transaction=None) -> None:
        doomed = [k for k, b in self.blocks.items() if b.get("recordId") in set(record_ids)]
        await self.delete_edges_touching_nodes(doomed, CollectionNames.BLOCKS.value,
                                               CollectionNames.RECORD_LINKS.value)
        records = {f"{CollectionNames.RECORDS.value}/{r}" for r in record_ids}
        self.edges = [e for e in self.edges if e["_from"] not in records and e["_to"] not in records]
        for key in doomed:
            self.blocks.pop(key)

    async def batch_upsert_nodes(self, nodes, collection, transaction=None) -> bool:
        for node in nodes:
            doc = dict(node)
            doc["_key"] = doc.pop("id")
            self.blocks[doc["_key"]] = doc
        return True

    async def delete_nodes(self, keys, collection, transaction=None) -> bool:
        for key in keys:
            self.blocks.pop(key, None)
        return True

    async def batch_upsert_node_relations(self, edges, transaction=None) -> bool:
        # Both providers update a matching edge in place.
        index = {(e["_from"], e["_to"], e["relationshipType"]): e for e in self.edges}
        for edge in edges:
            key = (edge["_from"], edge["_to"], edge["relationshipType"])
            if key in index:
                index[key].update(edge)
            else:
                self.edges.append(edge)
                index[key] = edge
        return True

    # -- assertions helpers ---------------------------------------------

    def code_edges(self, relation=None) -> list[dict]:
        return [
            e for e in self.edges
            if e.get("source") == "code_graph"
            and (relation is None or e["relationshipType"] == relation)
        ]

    def edge_targets(self, relation) -> list:
        out = []
        for edge in self.code_edges(relation):
            _, _, ident = edge["_to"].partition("/")
            doc = self.blocks.get(ident)
            out.append(doc.get("qualifiedName") if doc else edge["_to"])
        return sorted(filter(None, out))


@pytest.fixture
def graph() -> FakeGraphProvider:
    return FakeGraphProvider()


@pytest.fixture
def index_file(graph) -> Callable[..., Awaitable[dict[str, int]]]:
    parser = CodeFileParser()

    async def _index(record_id, file_path, source, language, name=None) -> dict[str, int]:
        container = parser.parse_to_blocks(
            source, name or file_path.rsplit("/", 1)[-1], file_path, language
        )
        graph.code_files[record_id] = file_path
        graph.blobs[record_id] = {
            "block_containers": container.model_dump(mode="json", exclude_none=True)
        }
        ctx = BlockProjectionContext(
            org_id=ORG_ID,
            record_id=record_id,
            record_group_id=GROUP_ID,
            connector_id="conn1",
            language=language,
            file_path=file_path,
        )
        return await write_code_file_blocks_to_graph(
            graph_provider=graph, context=ctx, block_containers=container
        )

    return _index
