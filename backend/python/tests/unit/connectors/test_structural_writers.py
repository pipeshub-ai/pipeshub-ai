"""Every write of the transaction store that changes a hierarchy, group or inherit edge, or a node's access rule, is listed in
`_STRUCTURAL_WRITERS` and bumps the connector's scope generation once, in the same transaction, at its end. A writer added to
`GraphTransactionStore` that is neither listed nor declared harmless here fails `test_every_writer_is_classified`."""

from __future__ import annotations

import ast
import inspect
import pathlib
import re
import textwrap
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames
from app.connectors.core.base.data_store.graph_data_store import (
    _HARMLESS_WRITERS,
    _STRUCTURAL_WRITERS,
    GraphDataStore,
    GraphTransactionStore,
)
from tests.unit.connectors import graph_provider_classes as classes

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

R, G = CollectionNames.RECORDS.value, CollectionNames.RECORD_GROUPS.value


@pytest.fixture(autouse=True)
def _scope_fields_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """The bookkeeping exists only while the scope fields are on; the tests that show it off switch the setting back."""
    monkeypatch.setenv("ACL_SCOPE_FIELDS", "on")

HARMLESS = _HARMLESS_WRITERS

READ_PREFIXES = ("get_", "find_", "read_", "is_", "opens_", "filter_")


def _provider_writes(method) -> set[str]:
    """Provider calls a store method makes that are not reads: by what the method does, not by what it is called."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(inspect.unwrap(method))))
    called = {n.func.attr for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
              and isinstance(n.func.value, ast.Attribute) and n.func.value.attr == "graph_provider"}
    return {c for c in called if not c.startswith(READ_PREFIXES)}


def unclassified(cls: type) -> set[str]:
    out = set()
    for name, fn in inspect.getmembers(cls, inspect.iscoroutinefunction):
        if name.startswith("_") or name in _STRUCTURAL_WRITERS or name in HARMLESS:
            continue
        if _provider_writes(fn):
            out.add(name)
    return out


def test_every_writer_is_classified() -> None:
    assert not unclassified(GraphTransactionStore), "classify these (module._STRUCTURAL_WRITERS or HARMLESS)"


def test_a_writer_with_any_name_that_reaches_a_provider_write_is_flagged() -> None:
    class Scratch(GraphTransactionStore):
        async def move_record_under(self, record_id: str, parent_id: str) -> None:
            await self.graph_provider.batch_create_edges([{"to_id": record_id}], collection="recordRelations", transaction=self.txn)

        async def reparent_children(self, parent_id: str) -> None:
            await self.graph_provider.batch_upsert_node_relations([], transaction=self.txn)

        async def lookup_thing(self, key: str) -> None:
            await self.graph_provider.get_document(key, "records", transaction=self.txn)

    assert unclassified(Scratch) == {"move_record_under", "reparent_children"}


async def _by_field_in(collection, field, values, return_fields=None, transaction=None, **kwargs) -> list[dict]:
    """The batched read of the nodes' documents: every node belongs to connector c1."""
    return [{"_key": v, "connectorId": "c1"} for v in values]


def _stored_as(**fields: object) -> Callable[..., Awaitable[list[dict]]]:
    """The batched read, answering with stored documents that carry these fields."""
    async def read(collection, field, values, return_fields=None, transaction=None, **kwargs) -> list[dict]:
        return [{"_key": v, "connectorId": "c1", **fields} for v in values]

    return read


def _store() -> tuple[GraphTransactionStore, MagicMock]:
    provider = MagicMock()
    provider.get_document = AsyncMock(return_value={"connectorId": "c1"})
    provider.get_nodes_by_field_in = _by_field_in
    provider.get_nodes_by_field_in = _by_field_in
    provider.logger = MagicMock()
    return GraphTransactionStore(provider, "txn", explicit=True), provider


ARGS = {
    "create_record_groups_relation": ("g1", "g2"),
    "delete_record_by_key": ("r1",),
    "delete_edge": ("r1", R, "r2", R, CollectionNames.NODE_RELATIONS.value),
    "delete_nodes": (["r1"], R),
    "delete_edges_from": ("r1", R, CollectionNames.BELONGS_TO.value),
    "delete_edges_to": ("r1", R, CollectionNames.NODE_RELATIONS.value),
    "replace_edges_to": ("r1", R, [], CollectionNames.INHERIT_PERMISSIONS.value),
    "replace_record_permissions": ("r1", [], "g1", {"inherit": True}),
    "link_record_to_group": ("r1", "g1", {"inherit": True}),
    "delete_parent_child_edge_to_record": ("r1",),
    "delete_edges_to_groups": ("r1", R, CollectionNames.BELONGS_TO.value),
    "delete_edges_between_collections": ("r1", R, CollectionNames.INHERIT_PERMISSIONS.value, G),
    "delete_edges_by_relationship_types": ("r1", R, CollectionNames.NODE_RELATIONS.value, ["PARENT_CHILD"]),
    "delete_nodes_and_edges": (["g1"], G),
    "delete_single_record": ("r1",),
    "create_record_relation": ("r1", "r2", "PARENT_CHILD"),
    "create_record_group_relation": ("r1", "g1"),
    "create_inherit_permissions_relation_record_group": ("r1", "g1"),
    "delete_inherit_permissions_relation_record_group": ("r1", "g1"),
    "create_inherit_permissions_relation_record": ("r1", "r2"),
    "delete_inherit_permissions_relation_record": ("r1", "r2"),
    "batch_create_edges": ([{"from_id": "r1", "from_collection": R, "to_id": "g1", "to_collection": G}], CollectionNames.BELONGS_TO.value),
    "create_edges_if_absent": ([{"from_id": "r1", "from_collection": R, "to_id": "r2", "to_collection": R}], CollectionNames.INHERIT_PERMISSIONS.value),
    "batch_delete_edges": ([{"from_id": "r1", "from_collection": R, "to_id": "g1", "to_collection": G}], CollectionNames.BELONGS_TO.value),
    "batch_upsert_node_relations": ([{"from_id": "r1", "to_id": "r2", "to_collection": R, "relationshipType": "PARENT_CHILD"}],),
    "delete_records_and_relations": ("r1",),
    "upsert_record_under_parent": (SimpleNamespace(id="r1"), "r0"),
    "batch_upsert_record_permissions": ("r1", []),
    "batch_upsert_record_group_permissions": ("g1", [], "c1"),
}


@pytest.mark.parametrize("name", sorted(_STRUCTURAL_WRITERS))
async def test_a_structural_writer_records_its_connector(name: str) -> None:
    store, provider = _store()
    call = AsyncMock(return_value=None)

    class Provider:
        logger = provider.logger
        get_document = provider.get_document
        get_nodes_by_field_in = staticmethod(_by_field_in)
        n = 0

        async def get_structure_of_nodes(self, collection, ids, **k) -> dict:  # the structure differs after the write
            Provider.n += 1
            return {i: frozenset({("belongsTo", i, f"y{Provider.n}")}) for i in ids}

        async def get_edges_from_node(self, *a, **k) -> list[dict]:  # edges at the parent end of a node, for the writers that name one
            return [{"_from": "x", "_to": "records/r9", "relationshipType": "PARENT_CHILD"}]

        async def get_edges_to_node(self, *a, **k) -> list[dict]:
            return [{"_from": "records/r9", "_to": "x", "relationshipType": "PARENT_CHILD"}]

        def __getattr__(self, _name: str) -> AsyncMock:  # whatever provider call the writer delegates to answers
            return call

    store.graph_provider = Provider()
    args = list(ARGS[name])
    kwargs = args.pop() if args and isinstance(args[-1], dict) else {}
    await getattr(store, name)(*args, **kwargs)
    assert store.structural_connectors == {"c1"}, name


async def test_a_grant_is_structural_only_when_it_flips_whether_the_node_has_one() -> None:
    perm = CollectionNames.PERMISSION.value
    edge = {"from_id": "u1", "from_collection": "users", "to_id": "r1", "to_collection": R}
    seen = {"calls": 0}

    def provider_answering(structures: list[frozenset]):  # noqa: ANN202
        async def structure(collection, ids, **kwargs) -> dict:
            i = min(seen["calls"], len(structures) - 1)
            seen["calls"] += 1
            return dict.fromkeys(ids, structures[i])
        return structure

    async def run(structures: list[frozenset], writer: str = "batch_create_edges") -> set[str]:
        seen["calls"] = 0
        store, provider = _store()
        store.graph_provider = SimpleNamespace(get_nodes_by_field_in=_by_field_in, logger=provider.logger,
                                               get_structure_of_nodes=provider_answering(structures),
                                               batch_create_edges=AsyncMock(), batch_delete_edges=AsyncMock())
        await getattr(store, writer)([edge], perm)
        return store.structural_connectors

    none, one = frozenset(), frozenset({("granted", "r1")})
    assert await run([none, one]) == {"c1"}  # the first grant
    assert await run([one, none], "batch_delete_edges") == {"c1"}  # the last one removed
    assert await run([one, one]) == set()  # another grant on a node that has one
    assert await run([none, none]) == set()
    store, provider = _store()
    store.graph_provider = SimpleNamespace(get_nodes_by_field_in=_by_field_in, logger=provider.logger, batch_create_edges=AsyncMock())
    await store.batch_create_edges([{**edge, "to_collection": "agents"}], perm)  # a grant on an agent reaches no stamp
    await store.batch_create_edges([{"from_id": "r1", "from_collection": R, "to_id": "r2", "to_collection": R,
                                     "relationshipType": "LINKED_TO"}], CollectionNames.NODE_RELATIONS.value)  # a link is not the hierarchy
    assert store.structural_connectors == set()


async def test_an_edge_that_names_no_node_fails_loudly() -> None:
    store, provider = _store()
    store.graph_provider = SimpleNamespace(get_nodes_by_field_in=_by_field_in, logger=provider.logger, batch_create_edges=AsyncMock())
    with pytest.raises(ValueError, match="names no node"):
        await store.batch_create_edges([{"from_collection": R}], CollectionNames.BELONGS_TO.value)


async def test_the_connector_is_found_before_a_delete_removes_the_node() -> None:
    store, provider = _store()
    seen = []

    async def delete(*a, **k) -> None:
        seen.append(set(store.structural_connectors))

    store.graph_provider = SimpleNamespace(get_document=provider.get_document, get_nodes_by_field_in=_by_field_in, logger=provider.logger, delete_single_record=delete)
    await store.delete_single_record("r1")
    assert seen == [{"c1"}]








async def test_an_upsert_that_changes_the_access_rule_of_an_existing_record_is_structural() -> None:
    store, provider = _store()
    store.graph_provider = SimpleNamespace(get_nodes_by_field_in=_stored_as(accessRule="STRICT"), logger=provider.logger,
                                           batch_upsert_records=AsyncMock(), batch_upsert_record_groups=AsyncMock())
    same = SimpleNamespace(id="r1", connector_id="c1", access_rule=SimpleNamespace(value="STRICT"), external_record_id="e", )
    await store.batch_upsert_records([same])
    assert store.structural_connectors == set()
    await store.batch_upsert_records([SimpleNamespace(id="r1", connector_id="c1", access_rule=SimpleNamespace(value="RESTRICTED"),
                                                      external_record_id="e")])
    assert store.structural_connectors == {"c1"}


async def test_a_node_patch_is_structural_by_its_field_not_its_method() -> None:
    store, provider = _store()
    store.graph_provider = SimpleNamespace(get_nodes_by_field_in=_stored_as(accessRule="OPEN"), logger=provider.logger, batch_update_nodes=AsyncMock(),
                                           batch_upsert_nodes=AsyncMock())
    await store.batch_update_nodes([{"_key": "r1", "name": "x"}], R)  # content only
    await store.batch_update_nodes([{"_key": "r1", "accessRule": "OPEN"}], R)  # the same rule
    await store.batch_update_nodes([{"_key": "c1", "scopeProjectionGeneration": 5}], CollectionNames.APPS.value)
    assert store.structural_connectors == set()
    await store.batch_upsert_nodes([{"_key": "r1", "accessRule": "RESTRICTED"}], R)
    assert store.structural_connectors == {"c1"}


async def test_a_stored_node_without_a_rule_counts_its_default_and_an_unchanged_edge_write_is_not_structural() -> None:
    store, provider = _store()
    unchanged = frozenset({("belongsTo", "r1", "g0")})

    async def structure(collection, ids, **kwargs) -> dict:
        return dict.fromkeys(ids, unchanged)

    # no accessRule, no permissionModel stored
    store.graph_provider = SimpleNamespace(get_nodes_by_field_in=_stored_as(), logger=provider.logger,
                                           batch_upsert_records=AsyncMock(), batch_upsert_record_groups=AsyncMock(),
                                           get_structure_of_nodes=structure, create_record_group_relation=AsyncMock())
    same = SimpleNamespace(id="r1", connector_id="c1", access_rule=SimpleNamespace(value="OPEN"))
    await store.batch_upsert_records([same])
    await store.create_record_group_relation("r1", "g1")  # the edge is there already: nothing changed
    assert store.structural_connectors == set()
    await store.batch_upsert_records([SimpleNamespace(id="r1", connector_id="c1", access_rule=SimpleNamespace(value="RESTRICTED"))])
    assert store.structural_connectors == {"c1"}
    store.structural_connectors.clear()
    await store.batch_upsert_record_groups([SimpleNamespace(id="g1", connector_id="c1", access_rule=SimpleNamespace(value="OPEN"),
                                                            permission_model=SimpleNamespace(value="RECORD_GROUP_LEVEL"))])
    assert store.structural_connectors == {"c1"}


# ---- the proxy: the store has no other handle on the provider ---------------------------------------------------------------

async def _helper(provider, txn) -> None:
    await provider.batch_create_edges([], collection="recordRelations", transaction=txn)


class _Scratch(GraphTransactionStore):
    """Unlisted writers in every shape that evades a reader of the source."""

    async def via_local_alias(self, record_id: str) -> None:
        gp = self.graph_provider
        await gp.batch_create_edges([{"to_id": record_id}], collection="recordRelations", transaction=self.txn)

    async def via_raw_client(self, record_id: str) -> None:
        await self.graph_provider.client.execute_query("MATCH (r:Record {id: $id}) SET r.accessRule = 'OPEN'", parameters={"id": record_id})

    async def via_getattr(self, record_id: str) -> None:
        await getattr(self.graph_provider, "batch_delete_edges")([], collection="inheritPermissions", transaction=self.txn)

    async def via_module_helper(self, record_id: str) -> None:
        await _helper(self.graph_provider, self.txn)

    async def via_read_named_writer(self, record_id: str) -> None:
        await self.graph_provider.get_or_create_parent_edge(record_id, transaction=self.txn)

    async def via_execute_query(self, record_id: str) -> None:
        await self.graph_provider.execute_query("MATCH (r) DETACH DELETE r", transaction=self.txn)

    async def via_bound_method(self, record_id: str) -> None:
        write = self.graph_provider.batch_create_edges
        await write([], collection="belongsTo", transaction=self.txn)


@pytest.mark.parametrize("shape", sorted(n for n in vars(_Scratch) if n.startswith("via_")))
async def test_an_unclassified_provider_write_is_caught_whatever_its_shape(shape: str) -> None:
    from app.connectors.core.base.data_store.graph_data_store import (
        UnclassifiedProviderWrite,
    )

    provider = MagicMock()
    for name in ("batch_create_edges", "batch_delete_edges", "execute_query", "get_or_create_parent_edge"):
        setattr(provider, name, AsyncMock())
    provider.client.execute_query = AsyncMock()
    store = _Scratch(provider, "txn", explicit=True)
    with pytest.raises(UnclassifiedProviderWrite):
        await getattr(store, shape)("r1")


async def test_reads_and_classified_writers_pass_through_the_proxy() -> None:
    provider = MagicMock()
    provider.get_document = AsyncMock(return_value={"connectorId": "c1"})
    provider.get_nodes_by_field_in = _by_field_in
    provider.get_edges_from_node = AsyncMock(return_value=[])
    provider.get_edges_to_node = AsyncMock(return_value=[])
    provider.delete_single_record = AsyncMock(return_value={"success": True})
    provider.batch_upsert_nodes = AsyncMock(return_value=True)
    store = GraphTransactionStore(provider, "txn", explicit=True)
    assert await store.graph_provider.get_document("r1", "records") == {"connectorId": "c1"}
    await store.delete_single_record("r1")
    await store.batch_upsert_nodes([{"_key": "x", "name": "y"}], "records")
    provider.delete_single_record.assert_awaited_once()




async def test_a_failed_lookup_in_a_wrapper_fails_the_block_instead_of_looking_like_a_new_node() -> None:
    provider = MagicMock()
    provider.logger = MagicMock()
    provider.get_document = AsyncMock(side_effect=RuntimeError("graph down"))
    provider.get_nodes_by_field_in = AsyncMock(side_effect=RuntimeError("graph down"))
    store = GraphTransactionStore(provider, "txn", explicit=True)
    with pytest.raises(RuntimeError):
        await store.create_record_group_relation("r1", "g1")
    assert store.structural_connectors == set() and not store._connector_of  # nothing cached from the failure
    assert provider.get_nodes_by_field_in.await_args.kwargs["raise_on_error"] is True


# ---- provider writes made outside the transaction store ------------------------------------------------------------------------
# Like the org-context guard: the callers of a structural provider write outside `GraphTransactionStore` are listed here with the reason each
# is safe. A new one fails the test. No reader of the structure generation may land until every structural writer is either routed through
# the store or listed with a reason that holds. The set of structural writers is derived from the provider classes themselves, not kept by
# hand: every public method of `IGraphDBProvider` that is not a read and whose Neo4j or ArangoDB implementation writes (CREATE, MERGE,
# DELETE, UPSERT, UPDATE ...) over a hierarchy, group, inherit or full-sync edge collection, plus every writer the store classifies.
_APP = pathlib.Path(__file__).resolve().parents[3] / "app"


def _public_provider_methods() -> set[str]:
    import inspect

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

    return {n for n, _fn in inspect.getmembers(IGraphDBProvider, inspect.isfunction) if not n.startswith("_")}


def test_every_provider_method_is_classified() -> None:
    """Closed world: a method added to `IGraphDBProvider` cannot land until someone says whether it is a read, a harmless write, a
    structural write or a generic patch (`graph_provider_classes.py`)."""
    methods = _public_provider_methods()
    groups = [classes.READS, classes.HARMLESS, classes.STRUCTURAL, classes.PATCH]
    classified = classes.READS | classes.HARMLESS | classes.STRUCTURAL | classes.PATCH
    assert methods - classified == set(), f"classify these provider methods in graph_provider_classes.py: {sorted(methods - classified)}"
    assert classified - methods == set(), f"classified but not on the interface any more: {sorted(classified - methods)}"
    assert sum(len(c) for c in groups) == len(classified), "a method is in two classes"


def test_the_store_and_the_classes_agree() -> None:
    methods = _public_provider_methods()
    listed = {n for n in _STRUCTURAL_WRITERS if n in methods}
    assert listed <= (classes.STRUCTURAL | classes.PATCH), f"the store treats these as structural, the classes do not: {sorted(listed - classes.STRUCTURAL - classes.PATCH)}"
    assert (classes.STRUCTURAL | classes.PATCH) & set(_HARMLESS_WRITERS) <= {
        "batch_update_nodes", "batch_upsert_nodes", "batch_upsert_records", "batch_upsert_record_groups", "delete_records_recursive",
        "soft_delete_records", "restore_records", "delete_record_by_external_id", "remove_user_access_to_record", "take_back_kept_record_group",
        "remove_nodes_by_field",
    }, "a writer the store calls harmless is structural or a patch in the classes (these have wrappers of their own)"


# Methods the scan below flags that are reads or harmless writes anyway: AQL's MERGE() function is not a write.
_SCAN_REVIEWED = {
    "get_app_users": "RETURN MERGE(user, {...}) is the AQL function",
    "get_knowledge_hub_root_nodes_v2": "RETURN MERGE(node, {...}) is the AQL function",
    "get_node_relations_batch": "RETURN MERGE(e, {...}) is the AQL function",
    "list_kb_records": "MERGE(FOR f IN ... ) is the AQL function",
}


def _scan_flags() -> set[str]:
    """Cross-check of the classes: the methods whose Neo4j or ArangoDB source names a hierarchy, group, inherit or full-sync edge collection and
    writes (a Cypher or AQL write keyword, in capitals)."""
    import inspect

    from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
    from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

    tokens = re.compile(r"\b(NODE_RELATIONS|BELONGS_TO|INHERIT_PERMISSIONS|FULL_SYNC_EDGE_COLLECTIONS|PARENT_CHILD|HIERARCHY_RELATION_TYPES|NODE_RELATION)\b")
    writes = re.compile(r"\b(DELETE|DETACH|MERGE|CREATE|REMOVE|SET|INSERT|UPDATE|UPSERT|REPLACE)\b")
    found = set()
    for name in _public_provider_methods():
        source = ""
        for impl in (Neo4jProvider, ArangoHTTPProvider):
            fn = getattr(impl, name, None)
            if fn is not None:
                try:
                    source += inspect.getsource(inspect.unwrap(fn))
                except (OSError, TypeError):
                    continue
        if tokens.search(source) and writes.search(source):
            found.add(name)
    return found


def test_no_method_the_scan_thinks_writes_edges_is_classified_a_read_or_harmless() -> None:
    contradicted = {n for n in _scan_flags() if n in (classes.READS | classes.HARMLESS) and n not in _SCAN_REVIEWED}
    assert not contradicted, f"the source of these writes over structural edges, but they are classified as reads or harmless: {sorted(contradicted)}"
    assert set(_SCAN_REVIEWED) <= _scan_flags(), "remove the reviewed entries the scan no longer flags"








def test_the_classes_hold_the_writers_that_were_missed() -> None:
    assert {"unlink_nodes_missed_by_full_sync", "backfill_hierarchy", "drop_redundant_group_edges", "purge_trash_kept_record_groups",
            "delete_connector_instance", "link_record_to_group", "execute_query"} <= classes.STRUCTURAL










# ---- the setting is off: the store does exactly what it did before ---------------------------------------------------------------
def _recording_provider() -> MagicMock:
    provider = MagicMock()
    provider.logger = MagicMock()
    provider.begin_transaction = AsyncMock(return_value="t")
    provider.opens_explicit_transaction = MagicMock(return_value=True)
    provider.commit_transaction = AsyncMock()
    provider.rollback_transaction = AsyncMock()
    provider.is_write_conflict = MagicMock(return_value=False)
    for name in ("batch_create_edges", "delete_edges_from", "delete_edges_to", "replace_edges_to", "delete_single_record", "batch_upsert_nodes",
                 "batch_update_nodes", "batch_upsert_records", "batch_upsert_record_groups", "link_record_to_group", "update_node",
                 "create_edges_if_absent", "batch_upsert_node_relations", "soft_delete_records", "delete_nodes", "claim_push_record"):
        setattr(provider, name, AsyncMock(return_value=None))
    # anything else the bookkeeping would ask for answers, so a call to it is visible rather than an error
    provider.get_document = AsyncMock(return_value={"connectorId": "c1"})
    provider.get_nodes_by_field_in = AsyncMock(side_effect=_by_field_in)
    provider.get_structure_of_nodes = AsyncMock(return_value={})
    provider.get_edges_from_node = AsyncMock(return_value=[])
    provider.get_edges_to_node = AsyncMock(return_value=[])
    provider.get_org_apps = AsyncMock(return_value=[])
    provider.increment_node_field = AsyncMock(return_value=1)
    return provider


async def _a_sync_through_the_store(provider: MagicMock) -> None:
    store = GraphDataStore(MagicMock(), provider)
    async with store.transaction() as tx:
        edge = {"from_id": "r1", "from_collection": R, "to_id": "g1", "to_collection": G}
        await tx.batch_create_edges([edge], CollectionNames.BELONGS_TO.value)
        await tx.create_edges_if_absent([edge], CollectionNames.BELONGS_TO.value)
        await tx.delete_edges_from("r0", R, CollectionNames.NODE_RELATIONS.value)
        await tx.delete_edges_to("g1", G, CollectionNames.BELONGS_TO.value)
        await tx.replace_edges_to("g1", G, [edge], CollectionNames.BELONGS_TO.value)
        await tx.batch_upsert_records([SimpleNamespace(id="r1", connector_id="c1", access_rule=SimpleNamespace(value="RESTRICTED"))])
        await tx.batch_update_nodes([{"_key": "r1", "accessRule": "RESTRICTED"}], R)
        await tx.delete_single_record("r1")
        await tx.soft_delete_records(["r1"], "c1", delete_source="USER", batch_id="b1")


async def test_with_the_setting_off_the_store_makes_exactly_the_calls_it_made_before(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ACL_SCOPE_FIELDS")
    provider = _recording_provider()
    await _a_sync_through_the_store(provider)
    called = {c[0] for c in provider.mock_calls if "." not in c[0]}
    # the writers' own delegated calls and the transaction; no read of nodes, structure, edges or connectors, no app-document write
    assert called == {"begin_transaction", "opens_explicit_transaction", "commit_transaction", "batch_create_edges", "create_edges_if_absent",
                      "delete_edges_from", "delete_edges_to", "replace_edges_to", "batch_upsert_records", "batch_update_nodes",
                      "delete_single_record", "soft_delete_records"}, called
    assert not provider.update_node.await_count


async def test_with_the_setting_on_the_same_sync_reads_the_structure(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _recording_provider()
    provider.get_structure_of_nodes = AsyncMock(side_effect=[{"r1": frozenset()}, {"r1": frozenset({("belongsTo", "r1", "g1")})}] * 20)
    await _a_sync_through_the_store(provider)
    called = {c[0] for c in provider.mock_calls if "." not in c[0]}
    assert {"get_nodes_by_field_in", "get_structure_of_nodes"} <= called


async def test_the_setting_is_read_when_the_block_opens(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ACL_SCOPE_FIELDS")
    off = GraphTransactionStore(MagicMock(), "t", explicit=True)
    monkeypatch.setenv("ACL_SCOPE_FIELDS", "on")
    on = GraphTransactionStore(MagicMock(), "t", explicit=True)
    assert (off.tracked, on.tracked) == (False, True)




