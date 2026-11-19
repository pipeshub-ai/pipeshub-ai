"""The batched structure read of the transaction store (`get_structure_of_nodes`) on Neo4j and ArangoDB: it sees each kind of edge that decides a
node's access (hierarchy parent, group, inherit, and whether the node has any grant), and inside a transaction it sees that transaction's own
uncommitted writes, which is what the before and after comparison of a structural write relies on.

Needs the stores of the graph-permissions harness (`KH_PERM_*`).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames
from app.connectors.core.base.data_store.graph_data_store import GraphDataStore
from tests.integration.graph_permissions.processor_harness import build_processor
from tests.integration.graph_permissions.test_write_path import _group, _record
from tests.integration.graph_permissions.write_path_support import (
    ORG,
    _ArangoBackend,
    _Neo4jBackend,
    _seed_app,
    _seed_user,
    _suffix,
)

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]
R, G = CollectionNames.RECORDS.value, CollectionNames.RECORD_GROUPS.value
REL, BEL, INH, PERM = (CollectionNames.NODE_RELATIONS.value, CollectionNames.BELONGS_TO.value,
                       CollectionNames.INHERIT_PERMISSIONS.value, CollectionNames.PERMISSION.value)


@pytest.fixture(autouse=True)
def _scope_fields_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACL_SCOPE_FIELDS", "on")


@pytest.fixture(params=["neo4j", "arango"])
def backend(request, arango_provider, arango_settings, neo4j_provider, neo4j_settings) -> object:
    if request.param == "arango":
        return _ArangoBackend(arango_provider, arango_settings)
    return _Neo4jBackend(neo4j_provider, neo4j_settings)


async def _world(backend) -> tuple:
    processor, _ = build_processor(backend.provider, ORG)
    conn = f"conn-{_suffix()}"
    await _seed_app(backend, conn)
    space = f"space-{_suffix()}"
    await processor.on_new_record_groups([(_group(space, inherit=True, connector_id=conn), [])])
    page = _record(f"page-{_suffix()}", connector_id=conn, group_external=space)
    child = _record(f"child-{_suffix()}", connector_id=conn, parent_external=page.external_record_id, group_external=space)
    await processor.on_new_records([(page, []), (child, [])])
    return conn, page, child


async def test_the_structure_holds_the_parent_the_group_the_inherit_edge_and_whether_there_is_a_grant(backend) -> None:
    _, page, child = await _world(backend)
    got = await backend.provider.get_structure_of_nodes(R, [page.id, child.id])
    kinds = {e[0] for e in got[child.id]}
    assert {REL, BEL, INH} <= kinds and not any(e[0] == "granted" for e in got[child.id])
    assert any(e[0] == REL and e[1] == page.id for e in got[child.id]), "the child's parent is the page"
    assert not any(e[0] == REL and e[1] == child.id for e in got[page.id]), "the page is not the child's child"
    user = await _seed_user(backend, f"member-{_suffix()}@example.com")
    store = GraphDataStore(MagicMock(), backend.provider)
    async with store.transaction() as tx:
        await tx.create_edges_if_absent([{"from_id": user, "from_collection": CollectionNames.USERS.value, "to_id": page.id,
                                          "to_collection": R}], PERM)
    assert ("granted", page.id) in (await backend.provider.get_structure_of_nodes(R, [page.id]))[page.id]
    assert await backend.provider.get_structure_of_nodes(R, []) == {}
    assert (await backend.provider.get_structure_of_nodes(R, ["no-such-record"])).get("no-such-record", frozenset()) == frozenset()


@pytest.mark.parametrize(("name", "remove"), [
    ("hierarchy parent", lambda tx, w: tx.delete_edges_to(w["child"], R, REL)),
    ("group", lambda tx, w: tx.delete_edges_from(w["child"], R, BEL)),
    ("inherit", lambda tx, w: tx.delete_edges_from(w["child"], R, INH)),
])
async def test_inside_a_transaction_the_read_sees_the_transactions_own_write(backend, name: str, remove) -> None:
    _, page, child = await _world(backend)
    store = GraphDataStore(MagicMock(), backend.provider)
    async with store.transaction(explicit=True) as tx:
        before = await tx._structures_of([(R, child.id)])
        await remove(tx, {"child": child.id})
        after = await tx._structures_of([(R, child.id)])
    assert before != after, name
    assert before[(R, child.id)] > after[(R, child.id)], "only edges went"


@pytest.mark.parametrize("removed", ["app first", "group first"])
async def test_an_app_and_a_record_group_with_one_key_are_two_ends_not_one(backend, removed: str) -> None:
    """The snapshot keeps the collection of the other end of an edge: removing either of two edges to `apps/x` and `recordGroups/x` changes
    it, whichever goes first (it did not when it kept the key alone)."""
    conn, _, child = await _world(backend)
    store = GraphDataStore(MagicMock(), backend.provider)
    same_key = _group(f"samekey-{_suffix()}", inherit=False, connector_id=conn)
    same_key.id = conn  # a record group whose key is the key of the app
    async with store.transaction() as tx:
        await tx.batch_upsert_record_groups([same_key])
    edge = {"from_id": child.id, "from_collection": R}
    app_edge = {**edge, "to_id": conn, "to_collection": CollectionNames.APPS.value}
    group_edge = {**edge, "to_id": conn, "to_collection": G}
    async with store.transaction() as tx:
        await tx.create_edges_if_absent([app_edge, group_edge], BEL)
    first, second = (app_edge, group_edge) if removed == "app first" else (group_edge, app_edge)
    async with store.transaction(explicit=True) as tx:
        s0 = (await tx._structures_of([(R, child.id)]))[(R, child.id)]
        await tx.batch_delete_edges([first], BEL)
        s1 = (await tx._structures_of([(R, child.id)]))[(R, child.id)]
        await tx.batch_delete_edges([second], BEL)
        s2 = (await tx._structures_of([(R, child.id)]))[(R, child.id)]
    assert s0 != s1 and s1 != s2, removed
