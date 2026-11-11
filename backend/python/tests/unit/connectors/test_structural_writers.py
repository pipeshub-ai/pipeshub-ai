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
from unittest.mock import ANY, AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames
from app.connectors.core.base.data_store.graph_data_store import (
    _CONNECTOR_WRITERS,
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
        if name.startswith("_") or name in _STRUCTURAL_WRITERS or name in _CONNECTOR_WRITERS or name in HARMLESS:
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
    "delete_user_group_by_id": ("ug1",),
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


async def test_the_transaction_bumps_each_touched_connector_once_before_it_commits(monkeypatch) -> None:
    order: list[str] = []
    provider = MagicMock()
    provider.logger = MagicMock()
    provider.get_document = AsyncMock(return_value={"connectorId": "c1"})
    provider.get_nodes_by_field_in = _by_field_in
    provider.begin_transaction = AsyncMock(return_value="t")
    provider.opens_explicit_transaction = MagicMock(return_value=True)
    provider.commit_transaction = AsyncMock(side_effect=lambda t: order.append("commit"))
    provider.delete_single_record = AsyncMock()
    provider.is_write_conflict = MagicMock(return_value=False)

    async def bump(tx, connector, roots=None) -> int:
        order.append(f"bump:{connector}")
        return 1

    from app.services.permissions import structure_generation

    monkeypatch.setattr(structure_generation, "bump_generation_in", bump)
    async with GraphDataStore(MagicMock(), provider).transaction() as tx:
        await tx.delete_single_record("r1")
        await tx.delete_single_record("r2")
    assert order == ["bump:c1", "commit"]
    order.clear()
    async with GraphDataStore(MagicMock(), provider).transaction() as tx:  # nothing structural: no bump
        pass
    assert order == ["commit"]


async def test_a_failed_bump_rolls_back_and_a_conflict_is_transient(monkeypatch) -> None:
    from app.services.permissions.structure_generation import ScopeGenerationConflict

    provider = MagicMock()
    provider.logger = MagicMock()
    provider.get_document = AsyncMock(return_value={"connectorId": "c1"})
    provider.get_nodes_by_field_in = _by_field_in
    provider.begin_transaction = AsyncMock(return_value="t")
    provider.opens_explicit_transaction = MagicMock(return_value=True)
    provider.rollback_transaction = AsyncMock()
    provider.commit_transaction = AsyncMock()
    provider.delete_single_record = AsyncMock()
    provider.is_write_conflict = MagicMock(return_value=False)
    provider.is_transient_error = MagicMock(return_value=False)

    async def bump(tx, connector, roots=None) -> int:
        raise ScopeGenerationConflict("x")

    from app.services.permissions import structure_generation

    monkeypatch.setattr(structure_generation, "bump_generation_in", bump)
    store = GraphDataStore(MagicMock(), provider)
    with pytest.raises(ScopeGenerationConflict) as err:
        async with store.transaction() as tx:
            await tx.delete_single_record("r1")
    provider.rollback_transaction.assert_awaited()
    provider.commit_transaction.assert_not_awaited()
    assert store.is_transient_error(err.value) is True  # so retry_on_deadlock runs the block again


async def test_without_explicit_transactions_a_failed_bump_marks_the_connector_unsafe(monkeypatch) -> None:
    provider = MagicMock()
    provider.logger = MagicMock()
    provider.get_document = AsyncMock(return_value={"connectorId": "c1"})
    provider.get_nodes_by_field_in = _by_field_in
    provider.begin_transaction = AsyncMock(return_value="t")
    provider.opens_explicit_transaction = MagicMock(return_value=False)  # NEO4J_EXPLICIT_TRANSACTIONS=false
    provider.rollback_transaction = AsyncMock()
    provider.update_node = AsyncMock()
    provider.delete_single_record = AsyncMock()
    provider.is_write_conflict = MagicMock(return_value=False)

    async def bump(tx, connector, roots=None) -> int:
        raise RuntimeError("down")

    from app.services.permissions import structure_generation

    monkeypatch.setattr(structure_generation, "bump_generation_in", bump)
    with pytest.raises(RuntimeError):
        async with GraphDataStore(MagicMock(), provider).transaction() as tx:
            await tx.delete_single_record("r1")
    provider.update_node.assert_awaited_with("c1", "apps", {"scopeProjectionUnsafe": ANY}, None)  # on its own: it must survive the failure


def _block_provider(explicit: bool) -> MagicMock:
    provider = MagicMock()
    provider.logger = MagicMock()
    provider.get_document = AsyncMock(return_value={"connectorId": "c1"})
    provider.get_nodes_by_field_in = _by_field_in
    provider.begin_transaction = AsyncMock(return_value="t")
    provider.opens_explicit_transaction = MagicMock(return_value=explicit)
    provider.rollback_transaction = AsyncMock()
    provider.update_node = AsyncMock()
    provider.delete_single_record = AsyncMock()
    provider.is_write_conflict = MagicMock(return_value=False)
    return provider


async def test_a_block_that_fails_after_a_structural_write_without_a_transaction_marks_the_connector_unsafe() -> None:
    """The write already committed, the bump is never reached: the connector must not stay trusted."""
    provider = _block_provider(explicit=False)
    with pytest.raises(RuntimeError):
        async with GraphDataStore(MagicMock(), provider).transaction() as tx:
            await tx.delete_single_record("r1")
            raise RuntimeError("connection reset after the first statement")
    provider.update_node.assert_awaited_with("c1", "apps", {"scopeProjectionUnsafe": ANY}, None)


async def test_a_block_that_fails_in_a_real_transaction_marks_nothing_because_the_rollback_undoes_it() -> None:
    provider = _block_provider(explicit=True)
    with pytest.raises(RuntimeError):
        async with GraphDataStore(MagicMock(), provider).transaction() as tx:
            await tx.delete_single_record("r1")
            raise RuntimeError("boom")
    provider.update_node.assert_not_awaited()
    provider.rollback_transaction.assert_awaited()


async def test_a_failed_block_with_no_structural_write_marks_nothing() -> None:
    provider = _block_provider(explicit=False)
    with pytest.raises(RuntimeError):
        async with GraphDataStore(MagicMock(), provider).transaction():
            raise RuntimeError("boom")
    provider.update_node.assert_not_awaited()


def test_a_conflict_on_the_counter_is_transient_only_when_the_block_ran_in_a_real_transaction() -> None:
    from app.services.permissions.structure_generation import ScopeGenerationConflict

    for explicit, expected in ((True, True), (False, False)):
        provider = _block_provider(explicit=explicit)
        provider.is_transient_error = MagicMock(return_value=False)
        assert GraphDataStore(MagicMock(), provider).is_transient_error(ScopeGenerationConflict("x")) is expected


async def test_the_parent_end_read_understands_the_generic_edge_shape_of_edges_to_a_node() -> None:
    """Neo4j answers `get_edges_to_node` with from_id / from_collection, not `_from`."""
    store, provider = _store()

    class Provider:
        logger = provider.logger
        get_document = provider.get_document

        @staticmethod
        async def get_nodes_by_field_in(collection, field, values, *a, **k) -> list[dict]:  # only the child end has a connector
            return [{"_key": v, "connectorId": "c9" if v == "r9" else None} for v in values]

        async def get_structure_of_nodes(self, collection, ids, **k) -> dict:
            return {i: frozenset() for i in ids}

        async def get_edges_to_node(self, *a, **k) -> list[dict]:
            return [{"from_id": "r9", "from_collection": R, "to_id": "r1", "to_collection": R}]

        delete_edges_to = AsyncMock()

    store.graph_provider = Provider()
    await store.delete_edges_to("r1", R, CollectionNames.INHERIT_PERMISSIONS.value)  # r1 is the parent end: r9 inherits from it
    assert store.structural_connectors == {"c9"}  # the connector of the other end, found from the generic shape


async def test_the_default_increment_answers_none_when_the_node_goes_away_between_the_read_and_the_write() -> None:
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

    node = SimpleNamespace(get_document=AsyncMock(return_value={"n": 4}), update_node=AsyncMock(return_value=False))
    assert await IGraphDBProvider.increment_node_field(node, "k", "apps", "n") is None
    node.update_node = AsyncMock(return_value=True)
    assert await IGraphDBProvider.increment_node_field(node, "k", "apps", "n") == 5


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


def _production_store(monkeypatch, *, doc: dict, org_apps: list[dict], explicit: bool = True):  # noqa: ANN202
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    provider = MagicMock()
    provider.logger = MagicMock()
    provider.get_document = AsyncMock(return_value=doc)
    provider.get_nodes_by_field_in = _by_field_in
    provider.get_org_apps = AsyncMock(return_value=org_apps)
    provider.begin_transaction = AsyncMock(return_value="t")
    provider.opens_explicit_transaction = MagicMock(return_value=explicit)
    provider.commit_transaction = AsyncMock()
    provider.rollback_transaction = AsyncMock()
    provider.update_node = AsyncMock()
    provider.batch_create_edges = AsyncMock()
    return provider, GraphDataStore(MagicMock(), provider)


async def test_in_production_an_unclassified_write_marks_every_connector_of_the_organisation_unsafe(monkeypatch) -> None:
    """Which connector the write touched is not known, so the ones the transaction saw are not enough: a write on `c2` after a lookup
    of `c1` would leave `c2` looking current."""
    from app.core import org_context

    provider, store_provider = _production_store(monkeypatch, doc={"connectorId": "c1"}, org_apps=[{"_key": "c1"}, {"_key": "c2"}, {"id": "c3"}])
    with org_context.bind(org_context.OrgContext("o1", None, "service", "test", "-")):
        async with store_provider.transaction() as tx:
            await tx._connector_of_node(R, "r1")  # the transaction has seen c1
            await _Scratch.via_local_alias(tx, "r1")  # an unclassified write: let through, but remembered
    provider.batch_create_edges.assert_awaited()
    provider.get_org_apps.assert_awaited_with("o1", active_only=False, raise_on_error=True)
    marked = {c.args[0] for c in provider.update_node.await_args_list if c.args[2] == {"scopeProjectionUnsafe": ANY}}
    assert marked == {"c1", "c2", "c3"}


async def test_an_unclassified_write_with_no_lookup_is_charged_to_the_ambient_organisation(monkeypatch) -> None:
    from app.core import org_context

    provider, store_provider = _production_store(monkeypatch, doc={}, org_apps=[{"_key": "c1"}, {"_key": "c2"}])
    with org_context.bind(org_context.OrgContext("o9", None, "service", "test", "-")):
        async with store_provider.transaction() as tx:
            await _Scratch.via_local_alias(tx, "r1")  # nothing looked up first
    provider.get_org_apps.assert_awaited_with("o9", active_only=False, raise_on_error=True)
    assert {c.args[0] for c in provider.update_node.await_args_list} == {"c1", "c2"}


async def test_an_unclassified_write_with_no_organisation_is_refused_before_it_reaches_the_provider(monkeypatch) -> None:
    from app.core.org_context import MissingOrgContext

    provider, store_provider = _production_store(monkeypatch, doc={"connectorId": "c1"}, org_apps=[{"_key": "c1"}, {"_key": "c2"}])
    with pytest.raises(MissingOrgContext):
        async with store_provider.transaction() as tx:
            await tx._connector_of_node(R, "r1")
            await _Scratch.via_local_alias(tx, "r1")
    provider.batch_create_edges.assert_not_awaited()  # nothing was written: there is nothing to mark or to undo
    provider.commit_transaction.assert_not_awaited()
    provider.get_org_apps.assert_not_awaited()


async def test_outside_a_transaction_an_unclassified_write_is_refused_when_the_organisations_connectors_cannot_be_listed(monkeypatch) -> None:
    from app.core import org_context

    provider, store_provider = _production_store(monkeypatch, doc={"connectorId": "c1"}, org_apps=[], explicit=False)
    provider.get_org_apps = AsyncMock(side_effect=RuntimeError("graph down"))
    with org_context.bind(org_context.OrgContext("o1", None, "service", "test", "-")), pytest.raises(RuntimeError):
        async with store_provider.transaction() as tx:
            await tx._connector_of_node(R, "r1")
            await _Scratch.via_local_alias(tx, "r1")
    provider.batch_create_edges.assert_not_awaited()  # it would have committed with nothing to undo or to mark


async def test_outside_a_transaction_every_connector_of_the_organisation_is_marked_before_an_unclassified_write_that_then_fails(monkeypatch) -> None:
    from app.core import org_context

    provider, store_provider = _production_store(
        monkeypatch, doc={"connectorId": "c1"}, org_apps=[{"_key": "c1"}, {"_key": "c2"}, {"id": "c3"}], explicit=False)
    order: list[str] = []

    async def write(*args, **kwargs) -> None:
        order.append("write")
        raise RuntimeError("connection reset")

    async def mark(*args, **kwargs) -> None:
        order.append("mark")

    provider.batch_create_edges = AsyncMock(side_effect=write)
    provider.update_node = AsyncMock(side_effect=mark)
    provider.increment_node_field = AsyncMock(side_effect=lambda key, collection, field, *a, **k: (order.append("mark"), 1)[1])
    # the listing works once (before the write) and is down when the failure handler would look again
    provider.get_org_apps = AsyncMock(side_effect=[[{"_key": "c1"}, {"_key": "c2"}, {"id": "c3"}], RuntimeError("graph down")])
    with org_context.bind(org_context.OrgContext("o1", None, "service", "test", "-")), pytest.raises(RuntimeError):
        async with store_provider.transaction() as tx:
            await tx._connector_of_node(R, "r1")
            await _Scratch.via_local_alias(tx, "r1")
    assert order[:4] == ["mark", "mark", "mark", "write"]  # every connector is marked before the write, which then fails
    assert set(order[4:]) <= {"mark"}
    opened = {c.args[0] for c in provider.increment_node_field.await_args_list if c.args[2] == "scopeProjectionOpened"}
    assert opened == {"c1", "c2", "c3"}  # every connector is opened before the write, also the ones the block never looked at
    assert all(c.args[3] is None for c in provider.update_node.await_args_list)  # on their own: they survive the failure


async def test_in_production_the_raw_client_is_refused_not_returned_unmarked(monkeypatch) -> None:
    """`graph_provider.client.execute_query(...)` would reach the database without the organisation's connectors marked first."""
    from app.connectors.core.base.data_store.graph_data_store import (
        UnclassifiedProviderWrite,
    )
    from app.core import org_context

    provider, store_provider = _production_store(monkeypatch, doc={"connectorId": "c1"}, org_apps=[{"_key": "c1"}], explicit=False)
    provider.client = MagicMock()
    provider.http_client = MagicMock()
    with org_context.bind(org_context.OrgContext("o1", None, "service", "test", "-")):
        async with store_provider.transaction() as tx:
            for name in ("client", "http_client"):
                with pytest.raises(UnclassifiedProviderWrite):
                    getattr(tx.graph_provider, name)
    provider.client.execute_query.assert_not_called()
    provider.update_node.assert_not_awaited()  # nothing was reached, so nothing needed marking


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
_MARKS = "marks the connector dirty after the write (note_structure_change / mark_scope_dirty)"
_OUTSIDE_THE_STORE: dict[tuple[str, str], str] = {
    # deletes an uploaded record through the provider: structural, so it moves the generation itself afterwards
    ("app/connectors/api/router.py", "delete_record"): _MARKS,
    # a full sync removes hierarchy, group, inherit and grant edges of the nodes it did not see: the generation moves afterwards
    ("app/connectors/core/sync/sync_runner.py", "unlink_nodes_missed_by_full_sync"): _MARKS,
    # removes record groups kept for the trash on connectors it cannot name: every connector of the organisation moves afterwards
    ("app/connectors/services/trash_purge.py", "purge_trash_kept_record_groups"): "marks every connector of the organisation (note_org_structure_change)",
    # one-off start-up migrations that write hierarchy or group edges for every connector: all connectors move afterwards
    ("app/migrations/hierarchy_backfill_migration.py", "backfill_hierarchy"): "marks every connector (note_org_structure_change)",
    ("app/migrations/redundant_group_edges_migration.py", "drop_redundant_group_edges"): "marks every connector (note_org_structure_change)",
    # removes the connector (an app) and its whole subtree: the app document goes, so there is no generation left to be current
    # and its points are purged by the connector-deletion events
    ("app/connectors/services/event_service.py", "delete_connector_instance"): "deletes the connector itself, and with it the generation",
    # the same for a knowledge base, which is an app
    ("app/connectors/sources/localKB/handlers/kb_service.py", "delete_connector_instance"): "deletes the knowledge base (an app) itself, as above",
    ("app/connectors/services/storage_release.py", "delete_connector_instance"): "finishes the delete of the connector itself, as above",
    # hard-deletes records that are already in the trash: the soft delete moved the generation and removed their points
    ("app/connectors/services/trash_purge.py", "purge_trashed_records"): "removes records already soft-deleted (that moved the generation)",
    # a push that finds its knowledge base being deleted removes the record it created a moment earlier in the same request;
    # that record was never indexed, so no point or stamp exists for it
    ("app/connectors/sources/localKB/push/service.py", "delete_nodes_and_edges"): "removes a record created in this request, never indexed: no stamp",
    # legacy knowledge bases become apps, and the old hub app goes: they have no stamps before they are apps
    ("app/migrations/kb_apps_migration.py", "migrate_legacy_kb_to_app"): "a legacy knowledge base becomes an app: no stamp can exist before",
    ("app/migrations/kb_apps_migration.py", "delete_kb_hub_app"): "removes an old hub app, which holds no records",
    # moves legacy edges to the current representation, link edges to recordLinks, and a user-to-app edge: the hierarchy is unchanged
    ("app/migrations/node_relation_migration.py", "migrate_legacy_relation_edge"): "the same edge in the current representation",
    ("app/migrations/record_link_migration.py", "split_link_edges"): "moves link relations to recordLinks, never PARENT_CHILD or ATTACHMENT",
    ("app/migrations/nextcloud_user_app_edges_migration.py", "delete_edge"): "a user-to-app edge, not a record or group",
    # copies the taxonomy relations (categories, departments, topics) of a record to its duplicate
    # rolls back / deletes conversation attachments: records created by the chat upload, which have no connector and are never stamped
    ("app/api/routes/chatbot.py", "delete_nodes_and_edges"): "conversation attachments: records with no connector and no stamp",
    # teams: a user-to-team permission edge, and a team node
    ("app/api/routes/entity.py", "batch_create_edges"): "user-to-team permission edges: a team is not a record or group",
    ("app/api/routes/entity.py", "delete_nodes"): "a team node",
    # agent templates, tools, toolsets, knowledge links: the collection is a variable holding agent resources, never records or groups
    ("app/api/routes/agent.py", "delete_nodes"): "agent resources, not records or record groups (the collection is a variable)",
    ("app/api/routes/agent.py", "delete_all_edges_for_node"): "edges of agent resources (servers, toolsets, templates, skills), never a record or group",
    ("app/modules/code_graph/block_projection.py", "delete_edges_touching_nodes"): "edges of code blocks, not records",
    ("app/modules/code_graph/edge_builder.py", "delete_edges_by_source_keys"): "code-block relations, not the hierarchy",
    ("app/api/routes/agent.py", "batch_create_edges"): "agent, toolset, tool and knowledge-link edges, and an agent's own permission edges",
    ("app/api/routes/chatbot.py", "batch_create_edges"): "permission and is-of-type edges of conversation attachments and conversations: no connector, never stamped",
    ("app/api/routes/chatbot.py", "batch_delete_edges"): "permission edges of conversations, as above",
    ("app/api/routes/chatbot.py", "delete_nodes"): "the files node of a deleted attachment",
    # the organisation-to-app edge of a new connector instance
    ("app/connectors/core/registry/connector_registry.py", "update_node"): "the permissionModel of a connector instance (an app), not of a record group",
    ("app/connectors/core/registry/connector_registry.py", "batch_create_edges"): "the organisation-to-app edge of a new connector instance",
    # the text is a docstring that names batch_create_edges; the edges it describes are permission and type edges
    ("app/agents/agent_loop/artifact_store.py", "batch_create_edges"): "a docstring; the edges are permission and is-of-type edges",
    # re-points the incoming link relations (foreign keys) of a stand-in table record, read from the recordLinks collection
    ("app/connectors/core/sql/graph_links.py", "batch_upsert_node_relations"): "link relations (foreign keys), never PARENT_CHILD or ATTACHMENT",
    # relations between code blocks (CONTAINS, DEFINES, METHOD, CALLS ...) in the blocks collection, never the record hierarchy
    ("app/modules/code_graph/block_projection.py", "batch_upsert_node_relations"): "code-block relations, never PARENT_CHILD or ATTACHMENT",
    ("app/modules/code_graph/block_projection.py", "delete_nodes"): "stale code blocks",
    ("app/modules/code_graph/edge_builder.py", "batch_upsert_node_relations"): "code-block relations, never PARENT_CHILD or ATTACHMENT",
    # the mapping rows of virtual records to stored documents: not records or groups
    ("app/modules/indexing/run.py", "delete_nodes"): "virtual-record mapping rows",
    ("app/modules/indexing/stored_content_cleanup.py", "delete_nodes"): "virtual-record mapping rows",
    ("app/services/vector_db/membership.py", "delete_nodes"): "virtual-record mapping rows",
    # a user's belongsTo edge to the organisation (user to org), not a record or a record group
    ("app/services/messaging/kafka/handlers/entity.py", "batch_create_edges"): "a user's belongsTo edge to the organisation",
    # example scripts of a connector family, never run by the service
    ("app/connectors/sources/google/drive/team/example.py", "batch_create_edges"): "an example script, not run by the service",
    ("app/connectors/sources/google/gmail/team/example.py", "batch_create_edges"): "an example script, not run by the service",
}


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
        "soft_delete_records", "restore_records", "delete_record_by_external_id", "take_back_kept_record_group",
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


def _calls(names: set[str]) -> dict[tuple[str, str], list[tuple[int, str]]]:
    """Calls of these provider methods outside the store, the providers and the scripts, with the text of each call."""
    call = re.compile(r"\b(?:graph_provider|provider|graph)\.(" + "|".join(sorted(names)) + r")\(")
    found: dict[tuple[str, str], list[tuple[int, str]]] = {}
    for path in sorted(_APP.rglob("*.py")):
        rel = "app/" + path.relative_to(_APP).as_posix()
        if rel.startswith(("app/services/graph_db/", "app/scripts/")) or rel.endswith("data_store/graph_data_store.py"):
            continue
        text = path.read_text(encoding="utf-8")
        for m in call.finditer(text):
            i, depth = m.end(), 1
            while i < len(text) and depth:
                depth += (text[i] == "(") - (text[i] == ")")
                i += 1
            found.setdefault((rel, m.group(1)), []).append((text.count("\n", 0, m.start()) + 1, text[m.start():i]))
    return found


_RULE_FIELD_NAMES = ("accessRule", "permissionModel", "isDeleted", "isPlaceholder", "externalParentId")


def _structural_calls_outside_the_store() -> dict[tuple[str, str], list[int]]:
    found = {k: [line for line, _t in v] for k, v in _calls(set(classes.STRUCTURAL)).items()}
    # a generic node patch that carries a field the projector reads (the literal name of one)
    for k, v in _calls(set(classes.PATCH)).items():
        lines = [line for line, text in v if any(f in text for f in _RULE_FIELD_NAMES)]
        if lines:
            found[k] = lines
    return found


async def test_a_user_losing_access_to_a_connector_moves_that_connector() -> None:
    """Every grant the user held on the connector goes; any of them may have been the last on its record or group."""
    store, provider = _store()
    provider.remove_user_access_to_connector = AsyncMock(return_value=3)
    assert await store.remove_user_access_to_connector("c9", "u1") == 3
    assert store.structural_connectors == {"c9"}


async def test_deleting_a_user_group_notes_the_connector_of_the_group() -> None:
    """The group goes with every grant it holds, and any of them may be the last on its node."""
    store, provider = _store()
    provider.delete_nodes_and_edges = AsyncMock()
    await store.delete_user_group_by_id("ug1")
    assert store.structural_connectors == {"c1"}
    assert "delete_user_group_by_id" not in HARMLESS


def test_the_connector_writers_are_structural_in_the_classes() -> None:
    assert set(_CONNECTOR_WRITERS) <= classes.STRUCTURAL
    assert "remove_user_access_to_connector" in classes.STRUCTURAL and "remove_user_access_to_connector" not in classes.HARMLESS


def test_the_classes_hold_the_writers_that_were_missed() -> None:
    assert {"unlink_nodes_missed_by_full_sync", "backfill_hierarchy", "drop_redundant_group_edges", "purge_trash_kept_record_groups",
            "delete_connector_instance", "link_record_to_group", "execute_query"} <= classes.STRUCTURAL


def test_no_structural_provider_write_outside_the_store_is_unlisted() -> None:
    unlisted = {k: v for k, v in _structural_calls_outside_the_store().items() if k not in _OUTSIDE_THE_STORE}
    assert not unlisted, f"route these through GraphTransactionStore, or move the generation and list them with a reason: {unlisted}"


def test_a_listed_caller_that_claims_to_mark_the_connector_does() -> None:
    for (rel, _name), reason in _OUTSIDE_THE_STORE.items():
        if reason.startswith("marks"):
            text = (_APP.parent / rel).read_text(encoding="utf-8")
            assert "note_structure_change(" in text or "note_org_structure_change(" in text or "mark_scope_dirty(" in text, rel


def test_every_listed_caller_still_exists() -> None:
    assert set(_OUTSIDE_THE_STORE) <= set(_structural_calls_outside_the_store()), "remove the entries of callers that are gone"


async def test_mark_scope_dirty_moves_the_generation_then_falls_back_to_unsafe_then_raises(monkeypatch) -> None:
    from app.services.permissions import structure_generation as sg

    monkeypatch.setattr(sg.asyncio, "sleep", AsyncMock())
    provider = MagicMock()
    provider.increment_node_field = AsyncMock(return_value=6)
    provider.update_node = AsyncMock(return_value=True)
    await sg.mark_scope_dirty(provider, "c1")
    assert provider.increment_node_field.await_args.args[:3] == ("c1", "apps", "scopeProjectionGeneration")
    await sg.mark_scope_dirty(provider, None)  # no connector: nothing to move
    provider.increment_node_field.side_effect = RuntimeError("down")
    await sg.mark_scope_dirty(provider, "c1")  # three failed bumps, then the unsafe mark lands
    assert provider.update_node.await_args.args[2] == {"scopeProjectionUnsafe": ANY}
    assert isinstance(provider.update_node.await_args.args[2]["scopeProjectionUnsafe"], int) and provider.update_node.await_args.args[2]["scopeProjectionUnsafe"] > 1
    provider.update_node.side_effect = RuntimeError("down")
    with pytest.raises(RuntimeError):
        await sg.mark_scope_dirty(provider, "c1")


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


async def test_with_the_setting_on_the_same_sync_reads_and_bumps(monkeypatch: pytest.MonkeyPatch) -> None:
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


@pytest.mark.parametrize("runner", ["execute_in_transaction", "execute_idempotent_in_transaction"])
async def test_both_retrying_runners_run_a_block_again_after_a_lost_race_for_the_generation(monkeypatch: pytest.MonkeyPatch, runner: str) -> None:
    from app.connectors.core.base.data_store import graph_data_store as module
    from app.services.permissions.structure_generation import ScopeGenerationConflict

    monkeypatch.setattr(module.asyncio, "sleep", AsyncMock())
    provider = _recording_provider()
    provider.is_transient_error = MagicMock(return_value=False)  # the provider itself calls nothing transient
    store = GraphDataStore(MagicMock(), provider)
    calls = {"n": 0}

    async def block(tx) -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            raise ScopeGenerationConflict("another writer moved the generation first")
        return "done"

    assert await getattr(store, runner)(block) == "done"
    assert calls["n"] == 2


@pytest.mark.parametrize(("outcome", "marked"), [((0, True), False), ((3, True), True), ((0, False), True), (RuntimeError("down"), True)])
async def test_a_full_sync_moves_the_generation_when_it_removed_edges_or_may_have(monkeypatch: pytest.MonkeyPatch, outcome: object, marked: bool) -> None:
    import logging

    from app.connectors.core.sync import sync_runner
    from app.connectors.core.sync.full_sync_coverage import (
        FullSyncCoverage,
        FullSyncSeen,
    )

    seen = FullSyncSeen()
    seen.add("records", "r1")
    provider = MagicMock()
    provider.unlink_nodes_missed_by_full_sync = AsyncMock(side_effect=outcome) if isinstance(outcome, Exception) else AsyncMock(return_value=outcome)
    provider.increment_node_field = AsyncMock(return_value=2)
    connector = SimpleNamespace(full_sync_coverage=FullSyncCoverage, data_entities_processor=None)
    await sync_runner._unlink_what_full_sync_missed(connector, "c1", provider, logging.getLogger("t"), seen)
    assert provider.increment_node_field.await_count == (1 if marked else 0)


class _AppDocs:
    """The app documents of a fake provider: update_node merges, increment_node_field is atomic (one statement, as on the three providers)."""

    def __init__(self, provider, ids: list[str], fail=lambda kind, key: False, missing: tuple[str, ...] = ()) -> None:
        self.docs: dict[str, dict] = {i: {} for i in ids}
        self.log: list[tuple[str, str, str]] = []
        self.fail = fail

        async def update(key, collection, updates, *args, **kwargs) -> bool:
            if self.fail("update", key):
                raise RuntimeError("graph down")
            if key in missing:                                       # every provider answers False for a document that is not there
                return False
            self.log.append(("update", key, ",".join(sorted(updates))))
            self.docs[key].update(updates)
            return True

        async def increment(key, collection, field, *args, **kwargs) -> int:
            if self.fail("increment:" + field, key):
                raise RuntimeError("graph down")
            self.log.append(("increment", key, field))
            self.docs[key][field] = int(self.docs[key].get(field) or 0) + 1
            return self.docs[key][field]

        provider.update_node = AsyncMock(side_effect=update)
        provider.increment_node_field = AsyncMock(side_effect=increment)


def _recorded_pass(doc: dict) -> dict:
    """What a finished pass leaves, from the generation and mark the document holds."""
    from app.services.permissions.scope_fields import (
        SCOPE_FIELDS_VERSION,
        current_scope_generation,
    )
    from app.services.vector_db.scope_backfill import BACKFILL_KEYS as K

    return {**doc, K["state"]: "done", K["version"]: SCOPE_FIELDS_VERSION, K["generation"]: current_scope_generation(), K["structure_generation"]: 0,
            K["unsafe"]: doc.get("scopeProjectionUnsafe")}


async def test_an_unclassified_write_outside_a_transaction_opens_before_it_runs_and_closes_after_the_mark_has_landed(monkeypatch) -> None:
    from app.core import org_context
    from app.services.permissions.currency import app_is_current

    provider, store_provider = _production_store(monkeypatch, doc={"connectorId": "c1"}, org_apps=[{"_key": "c1"}, {"_key": "c2"}], explicit=False)
    apps = _AppDocs(provider, ["c1", "c2"])

    async def the_write(*args, **kwargs) -> None:
        apps.log.append(("write", "-", "-"))
        for doc in apps.docs.values():              # during the write every connector is opened and not closed: nothing is current
            assert doc["scopeProjectionOpened"] == 1 and not doc.get("scopeProjectionClosed") and not app_is_current(_recorded_pass(doc))

    provider.batch_create_edges = AsyncMock(side_effect=the_write)
    with org_context.bind(org_context.OrgContext("o1", None, "service", "test", "-")):
        async with store_provider.transaction() as tx:
            await _Scratch.via_local_alias(tx, "r1")
    kinds = [(k, f) for k, _, f in apps.log]
    assert kinds.index(("write", "-")) > max(i for i, (k, f) in enumerate(kinds) if f == "scopeProjectionOpened")         # every opening step first
    for key in ("c1", "c2"):
        steps = [(k, f) for k, c, f in apps.log if c == key and k != "write"]
        assert steps == [("increment", "scopeProjectionOpened"), ("update", "scopeProjectionUnsafe"), ("increment", "scopeProjectionClosed")], key
        assert apps.docs[key]["scopeProjectionOpened"] == apps.docs[key]["scopeProjectionClosed"] == 1 and isinstance(apps.docs[key]["scopeProjectionUnsafe"], int)


async def test_a_closing_step_that_never_lands_leaves_the_connector_opened_and_not_current(monkeypatch) -> None:
    from app.core import org_context
    from app.services.permissions.currency import app_is_current

    provider, store_provider = _production_store(monkeypatch, doc={"connectorId": "c1"}, org_apps=[{"_key": "c1"}], explicit=False)
    monkeypatch.setattr("app.connectors.core.base.data_store.graph_data_store.asyncio.sleep", AsyncMock())
    apps = _AppDocs(provider, ["c1"], fail=lambda kind, key: kind == "increment:scopeProjectionClosed")

    async def failing_write(*args, **kwargs) -> None:
        raise RuntimeError("connection reset")

    provider.batch_create_edges = AsyncMock(side_effect=failing_write)
    with org_context.bind(org_context.OrgContext("o1", None, "service", "test", "-")), pytest.raises(RuntimeError, match="connection reset"):
        async with store_provider.transaction() as tx:
            await _Scratch.via_local_alias(tx, "r1")
    doc = apps.docs["c1"]
    assert doc["scopeProjectionOpened"] == 1 and not doc.get("scopeProjectionClosed")                  # opened, never closed
    assert not app_is_current(_recorded_pass(doc))


async def test_a_connector_is_not_closed_when_its_mark_did_not_land(monkeypatch) -> None:
    from app.core import org_context

    provider, store_provider = _production_store(monkeypatch, doc={"connectorId": "c1"}, org_apps=[{"_key": "c1"}, {"_key": "c2"}], explicit=False)
    monkeypatch.setattr("app.connectors.core.base.data_store.graph_data_store.asyncio.sleep", AsyncMock())
    apps = _AppDocs(provider, ["c1", "c2"], fail=lambda kind, key: kind == "update" and key == "c2")        # c2's mark never lands
    with org_context.bind(org_context.OrgContext("o1", None, "service", "test", "-")), pytest.raises(RuntimeError, match="graph down"):
        async with store_provider.transaction() as tx:
            await _Scratch.via_local_alias(tx, "r1")
    assert apps.docs["c1"].get("scopeProjectionClosed") == 1 and not apps.docs["c2"].get("scopeProjectionClosed")       # no closing step for c2
    assert apps.docs["c2"]["scopeProjectionOpened"] == 1


async def test_a_connector_whose_app_document_is_missing_is_not_closed_for_a_mark_that_was_not_written(monkeypatch) -> None:
    from app.core import org_context

    provider, store_provider = _production_store(monkeypatch, doc={"connectorId": "c1"}, org_apps=[{"_key": "c1"}], explicit=False)
    apps = _AppDocs(provider, ["c1"], missing=("c1",))                                  # update_node answers False, it does not raise
    with org_context.bind(org_context.OrgContext("o1", None, "service", "test", "-")), pytest.raises(RuntimeError):
        async with store_provider.transaction() as tx:
            await _Scratch.via_local_alias(tx, "r1")
            raise RuntimeError("boom")
    assert not apps.docs["c1"].get("scopeProjectionClosed") and not [e for e in apps.log if e[2] == "scopeProjectionClosed"]


async def test_a_closing_step_that_may_have_been_applied_is_not_tried_again(monkeypatch) -> None:
    """An exception from the closing increment proves nothing (a timeout can arrive after the store applied it): a second try would count a write
    that is still running as closed. Two overlapping writes: A's close is applied and then raises; the connector must stay not current."""
    from app.core import org_context
    from app.services.permissions.currency import app_is_current

    provider, store_provider = _production_store(monkeypatch, doc={"connectorId": "c1"}, org_apps=[{"_key": "c1"}], explicit=False)
    apps = _AppDocs(provider, ["c1"])
    plain = provider.increment_node_field.side_effect

    async def applies_then_raises(key, collection, field, *args, **kwargs) -> int:
        value = await plain(key, collection, field, *args, **kwargs)
        if field == "scopeProjectionClosed":
            raise TimeoutError("applied, but the answer never came")
        return value

    provider.increment_node_field = AsyncMock(side_effect=applies_then_raises)
    apps.docs["c1"]["scopeProjectionOpened"] = 1                                    # B opened earlier and is still running
    with org_context.bind(org_context.OrgContext("o1", None, "service", "test", "-")):
        async with store_provider.transaction() as tx:                              # A
            await _Scratch.via_local_alias(tx, "r1")
    doc = apps.docs["c1"]
    assert doc["scopeProjectionOpened"] == 2 and doc["scopeProjectionClosed"] == 1          # applied once, not retried
    assert not app_is_current(_recorded_pass(doc))                                          # B is still open


async def test_two_blocks_writing_at_once_each_count_and_the_connector_is_current_only_when_both_have_closed(monkeypatch) -> None:
    from app.services.permissions.currency import app_is_current

    provider, _ = _production_store(monkeypatch, doc={}, org_apps=[], explicit=False)
    apps = _AppDocs(provider, ["c1"])
    for _ in range(2):                                                  # A and B open
        await provider.increment_node_field("c1", "apps", "scopeProjectionOpened")
    await provider.update_node("c1", "apps", {"scopeProjectionUnsafe": 7})                # B's mark lands, a pass then records it
    await provider.increment_node_field("c1", "apps", "scopeProjectionClosed")            # B closes
    passed = _recorded_pass(apps.docs["c1"])
    assert not app_is_current(passed)                                                   # A's write is still in flight
    await provider.increment_node_field("c1", "apps", "scopeProjectionClosed")            # A closes (its own mark would follow)
    assert app_is_current(_recorded_pass(apps.docs["c1"]))
