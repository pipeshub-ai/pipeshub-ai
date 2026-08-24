"""Shared fixtures for the CodeGraph toolset tests.

A tiny in-memory graph: two blocks in two files, one CALLS edge between them,
one CONTAINS edge from a class group to a method.

A second, two-directory corner (`web/ui`, `web/api`) exists for the grouping
tests, which need buckets that differ -- an edge whose ends land in the same
bucket is a self-loop and is dropped.
"""
import pytest

from app.config.constants.arangodb import CollectionNames

BLOCKS = CollectionNames.BLOCKS.value
RECORDS = CollectionNames.RECORDS.value
ORG = "org-1"
USER = "user-1"


def _block(key, record_id, file_path, symbol_id, name, kind="function"):
    return {
        "_key": key,
        "id": key,
        "orgId": ORG,
        "recordId": record_id,
        "recordGroupId": "repo-1",
        "filePath": file_path,
        "symbolId": symbol_id,
        "qualifiedName": f"{kind}:{name}",
        "name": name,
        "kind": kind,
        "startLine": 1,
        "endLine": 5,
        "isBlockGroup": False,
    }


class FakeGraphProvider:
    """Implements only what the CodeGraph ops call."""

    def __init__(self, *, allow_user=USER, deny_records=()):
        self.allow_user = allow_user
        self.deny_records = set(deny_records)
        self.blocks = {
            "k_caller": _block("k_caller", "rec-a", "src/a.py", "src_a_caller", "caller"),
            "k_target": _block("k_target", "rec-b", "src/b.py", "src_b_target", "target"),
            "k_class": _block("k_class", "rec-b", "src/b.py", "src_b_thing", "Thing", "class"),
            "k_method": _block("k_method", "rec-b", "src/b.py", "src_b_thing_run", "run", "method"),
            "k_panel": _block("k_panel", "rec-c", "web/ui/panel.ts", "web_ui_panel_render",
                              "renderPanel"),
            # Named to collide with `k_method` so free-text ranking has to choose.
            "k_panel_imports": _block("k_panel_imports", "rec-c", "web/ui/panel.ts",
                                      "web_ui_panel_imports", "run", "imports"),
            "k_client": _block("k_client", "rec-d", "web/api/client.ts", "web_api_client_fetch",
                               "fetchTarget"),
        }
        self.blocks["k_class"]["isBlockGroup"] = True
        self.edges = [
            {"_from": f"{BLOCKS}/k_caller", "_to": f"{BLOCKS}/k_target", "relationshipType": "CALLS"},
            {"_from": f"{BLOCKS}/k_class", "_to": f"{BLOCKS}/k_method", "relationshipType": "CONTAINS"},
            {"_from": f"{BLOCKS}/k_target", "_to": f"{BLOCKS}/k_class", "relationshipType": "INHERITS"},
            # The imports block owns the edge, and it points at a whole record.
            {"_from": f"{BLOCKS}/k_panel_imports", "_to": f"{RECORDS}/rec-d",
             "relationshipType": "IMPORTS_FROM"},
            {"_from": f"{BLOCKS}/k_panel", "_to": f"{BLOCKS}/k_client",
             "relationshipType": "CALLS"},
        ]
        self.records = {
            "rec-a": {"_key": "rec-a", "virtualRecordId": "v-a"},
            "rec-b": {"_key": "rec-b", "virtualRecordId": "v-b"},
            "rec-c": {"_key": "rec-c", "virtualRecordId": "v-c"},
            "rec-d": {"_key": "rec-d", "virtualRecordId": "v-d"},
        }

    async def get_nodes_by_filters(self, collection, filters, return_fields=None, transaction=None):
        if collection != BLOCKS:
            return []
        return [d for d in self.blocks.values()
                if all(d.get(k) == v for k, v in filters.items())]

    async def get_nodes_by_field_in(self, collection, field_name, field_values,
                                    return_fields=None, transaction=None):
        wanted = set(field_values)
        return [d for d in self.blocks.values() if d.get(field_name) in wanted]

    async def get_document(self, document_key, collection):
        return self.records.get(document_key)

    async def check_record_access_with_details(self, user_id, org_id, record_id):
        if user_id != self.allow_user or record_id in self.deny_records:
            return None
        return {"record": {"_key": record_id}}

    async def get_neighbors_by_relationship_types(self, node_key, node_collection,
                                                  relationship_types, direction,
                                                  limit=25, transaction=None):
        anchor = f"{node_collection}/{node_key}"
        anchor_f, other_f = ("_from", "_to") if direction == "outbound" else ("_to", "_from")
        out = []
        for edge in self.edges:
            if edge.get(anchor_f) != anchor:
                continue
            if edge.get("relationshipType") not in relationship_types:
                continue
            collection, _, key = edge[other_f].partition("/")
            out.append({"collection": collection, "key": key,
                        "relationshipType": edge["relationshipType"]})
        return out[:limit]

    async def get_neighbors_for_nodes_by_relationship_types(self, node_keys, node_collection,
                                                            relationship_types, direction,
                                                            limit=5000, transaction=None):
        anchors = {f"{node_collection}/{k}" for k in node_keys}
        out = []
        for edge in self.edges:
            if edge.get("relationshipType") not in relationship_types:
                continue
            for anchor_f, other_f, label in (("_from", "_to", "outbound"),
                                             ("_to", "_from", "inbound")):
                if direction not in (label, "any"):
                    continue
                if edge.get(anchor_f) not in anchors:
                    continue
                collection, _, key = edge[other_f].partition("/")
                _, _, anchor_key = edge[anchor_f].partition("/")
                out.append({"anchorKey": anchor_key, "collection": collection, "key": key,
                            "direction": label, "relationshipType": edge["relationshipType"]})
        return out[:limit]


class FakeBlobStore:
    def __init__(self, records=None):
        self.records = records if records is not None else {
            "v-b": {
                "block_containers": {
                    "blocks": [{
                        "index": 0,
                        "code_metadata": {"symbol_id": "src_b_target"},
                        "data": {"text": "def target():\n    return 1\n", "subtokens": "target"},
                    }],
                    "block_groups": [],
                }
            }
        }

    async def get_record_from_storage(self, virtual_record_id, org_id):
        return self.records.get(virtual_record_id)


@pytest.fixture
def graph():
    return FakeGraphProvider()


@pytest.fixture
def blob():
    return FakeBlobStore()


class QueryGraphProvider(FakeGraphProvider):
    """Adds the query operations used by `query.py`."""

    async def get_accessible_virtual_record_ids(self, user_id, org_id, filters=None,
                                                time_range=None):
        if user_id != self.allow_user:
            return {}
        return {f"v-{r}": r for r in self.records if r not in self.deny_records}

    def _rollup(self, prefix, relations, direction, limit):
        """What the DB-side rollup returns: one row per file edge, with the
        record at each end so the caller can drop what it may not read."""
        by_id = {f"{BLOCKS}/{k}": d for k, d in self.blocks.items()}
        rows = []
        for edge in self.edges:
            if edge["relationshipType"] not in relations:
                continue
            for anchor_f, other_f, label in (("_from", "_to", "outbound"),
                                             ("_to", "_from", "inbound")):
                if direction not in (label, "any"):
                    continue
                block = by_id.get(edge[anchor_f])
                if not block or not (block.get("filePath") or "").startswith(prefix):
                    continue
                other = edge[other_f]
                target = by_id.get(other)
                collection, _, key = other.partition("/")
                rows.append({
                    "srcPath": block["filePath"], "srcRecord": block["recordId"],
                    "rel": edge["relationshipType"], "dir": label,
                    "dstPath": target["filePath"] if target else None,
                    "dstRecord": target["recordId"] if target else key,
                    "n": 1,
                })
        return rows[:limit]

    async def get_nodes_by_field_prefix(
        self, collection, field_name, prefix, filters=None, limit=400,
        transaction=None,
    ):
        return [
            doc for doc in self.blocks.values()
            if (doc.get(field_name) or "").startswith(prefix)
            and all(doc.get(key) == value for key, value in (filters or {}).items())
        ][:limit]

    async def search_nodes_by_field_terms(
        self, collection, field_name, terms, filters=None, limit=400,
        transaction=None,
    ):
        return [
            doc for doc in self.blocks.values()
            if doc.get(field_name)
            and any(term.lower() in doc[field_name].lower() for term in terms)
            and all(doc.get(key) == value for key, value in (filters or {}).items())
        ][:limit]

    async def get_edge_rollup_by_file_prefix(
        self, org_id, file_path_prefix, relationship_types, direction,
        limit=100000, transaction=None,
    ):
        return self._rollup(
            file_path_prefix, relationship_types, direction, limit
        )

    async def get_file_paths_for_records(
        self, org_id, record_ids, transaction=None,
    ):
        wanted = set(record_ids)
        paths = {}
        for doc in self.blocks.values():
            if (
                doc.get("orgId") == org_id
                and doc.get("recordId") in wanted
                and doc.get("filePath")
            ):
                paths.setdefault(doc["recordId"], doc["filePath"])
        return paths


@pytest.fixture
def query_graph():
    return QueryGraphProvider()
