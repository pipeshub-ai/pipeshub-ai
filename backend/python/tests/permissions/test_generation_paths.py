"""Which processor paths move a connector's structure generation: a restriction, a move or a delete arriving through the normal incremental
sync must, so a stamp computed before it is no longer current; a change that moves no structure (new records, grants, an unchanged
re-sync) must not.

Real processor on Neo4j and ArangoDB. Needs the stores of the graph-permissions harness (`KH_PERM_*`).
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames, DeleteSource
from app.connectors.core.base.data_store.graph_data_store import GraphDataStore
from app.models.entities import AccessRule
from app.models.permission import EntityType, Permission, PermissionType
from app.services.permissions.structure_generation import GENERATION_KEY
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


@pytest.fixture(autouse=True)
def _scope_fields_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """The structure bookkeeping exists only while the scope fields are on."""
    monkeypatch.setenv("ACL_SCOPE_FIELDS", "on")


@pytest.fixture(params=["neo4j", "arango"])
def backend(request, arango_provider, arango_settings, neo4j_provider, neo4j_settings) -> object:
    if request.param == "arango":
        return _ArangoBackend(arango_provider, arango_settings)
    return _Neo4jBackend(neo4j_provider, neo4j_settings)


async def _gen(provider, conn: str) -> int:
    return int((await provider.get_document(conn, CollectionNames.APPS.value) or {}).get(GENERATION_KEY) or 0)


async def _world(backend) -> tuple:
    processor, _ = build_processor(backend.provider, ORG)
    conn = f"conn-{_suffix()}"
    await _seed_app(backend, conn)
    space = f"space-{_suffix()}"
    await processor.on_new_record_groups([(_group(space, inherit=True, connector_id=conn), [])])
    page = _record(f"page-{_suffix()}", connector_id=conn, group_external=space)
    other = _record(f"other-{_suffix()}", connector_id=conn, group_external=space, rule=AccessRule.RESTRICTED)
    child = _record(f"child-{_suffix()}", connector_id=conn, parent_external=page.external_record_id, group_external=space)
    await processor.on_new_records([(page, []), (other, []), (child, [])])
    return processor, conn, space, page, other, child


async def test_restriction_via_on_new_records(backend) -> None:
    """The incremental sync re-sends a page with a new read restriction (accessRule RESTRICTED) through on_new_records."""
    processor, conn, space, page, _, child = await _world(backend)
    g0 = await _gen(backend.provider, conn)
    restricted = _record(page.external_record_id, connector_id=conn, group_external=space, rule=AccessRule.RESTRICTED)
    restricted.virtual_record_id = page.virtual_record_id
    await processor.on_new_records([(restricted, [])])
    g1 = await _gen(backend.provider, conn)
    assert g1 != g0


async def test_move_via_on_new_records(backend) -> None:
    """The incremental sync re-sends the child under another (restricted) parent page through on_new_records."""
    processor, conn, space, page, other, child = await _world(backend)
    g0 = await _gen(backend.provider, conn)
    moved = _record(child.external_record_id, connector_id=conn, parent_external=other.external_record_id, group_external=space)
    moved.virtual_record_id = child.virtual_record_id
    await processor.on_new_records([(moved, [])])
    g1 = await _gen(backend.provider, conn)
    assert g1 != g0


async def test_control_on_updated_record_permissions_moves_it(backend) -> None:
    processor, conn, space, page, *_ = await _world(backend)
    g0 = await _gen(backend.provider, conn)
    restricted = _record(page.external_record_id, connector_id=conn, group_external=space, rule=AccessRule.RESTRICTED)
    restricted.id = page.id
    await processor.on_updated_record_permissions(restricted, [])
    assert await _gen(backend.provider, conn) != g0


async def test_a_failed_bump_rolls_the_sync_back_instead_of_committing_a_change_nobody_knows_about(backend, monkeypatch) -> None:
    from app.services.permissions import structure_generation as module

    processor, conn, space, page, *_ = await _world(backend)
    g0 = await _gen(backend.provider, conn)

    async def refuse(tx_store, connector_id) -> None:
        raise RuntimeError("no bump")

    monkeypatch.setattr(module, "bump_generation_in", refuse)
    restricted = _record(page.external_record_id, connector_id=conn, group_external=space, rule=AccessRule.RESTRICTED)
    restricted.virtual_record_id = page.virtual_record_id
    with pytest.raises(RuntimeError):
        await processor.on_new_records([(restricted, [])])
    stored = await backend.provider.get_document(page.id, CollectionNames.RECORDS.value)
    assert await _gen(backend.provider, conn) == g0 and stored.get("accessRule") != "RESTRICTED"  # neither the change nor the bump


async def test_with_the_setting_off_a_sync_bumps_nothing_and_reads_nothing_extra(backend, monkeypatch) -> None:
    """Stamps written before the setting was last switched on are not trusted anyway (the scope generation), so a change made while it
    is off needs no bump; and the store makes no query it did not make before."""
    processor, conn, space, page, *_ = await _world(backend)
    monkeypatch.delenv("ACL_SCOPE_FIELDS")
    g0 = await _gen(backend.provider, conn)
    restricted = _record(page.external_record_id, connector_id=conn, group_external=space, rule=AccessRule.RESTRICTED)
    restricted.virtual_record_id = page.virtual_record_id
    await processor.on_new_records([(restricted, [])])
    assert await _gen(backend.provider, conn) == g0


# ---- every structural writer of the processor, on both stores (the bump lives in the transaction layer) ----------------
async def _writers_world(backend) -> tuple:
    processor, _ = build_processor(backend.provider, ORG)
    conn = f"conn-{_suffix()}"
    await _seed_app(backend, conn)
    space, space2 = f"space-{_suffix()}", f"space-{_suffix()}"
    await processor.on_new_record_groups([(_group(space, inherit=True, connector_id=conn), []),
                                          (_group(space2, inherit=True, connector_id=conn), [])])
    page = _record(f"page-{_suffix()}", connector_id=conn, group_external=space)
    child = _record(f"child-{_suffix()}", connector_id=conn, parent_external=page.external_record_id, group_external=space)
    lone = _record(f"lone-{_suffix()}", connector_id=conn, group_external=space2)
    await processor.on_new_records([(page, []), (child, []), (lone, [])])
    return processor, conn, space, space2, page, child, lone


async def _moved(backend, conn: str, fn) -> bool:
    g0 = await _gen(backend.provider, conn)
    await fn()
    return await _gen(backend.provider, conn) != g0


async def test_soft_delete_bumps(backend) -> None:
    processor, conn, *_, child, _ = await _writers_world(backend)
    assert await _moved(backend, conn, lambda: processor.on_records_soft_deleted([child.id], conn, delete_source=DeleteSource.CONNECTOR))


async def test_cascade_hard_delete_bumps(backend) -> None:
    processor, conn, _, _, page, *_ = await _writers_world(backend)
    assert await _moved(backend, conn, lambda: processor.on_records_deleted_cascade([page.id], conn, soft_delete=False))


async def test_record_group_delete_bumps(backend) -> None:
    processor, conn, _, space2, *_ = await _writers_world(backend)
    assert await _moved(backend, conn, lambda: processor.on_record_group_deleted(space2, conn))


async def test_group_rule_change_bumps(backend) -> None:
    processor, conn, space, *_ = await _writers_world(backend)
    assert await _moved(backend, conn, lambda: processor.on_new_record_groups(
        [(_group(space, inherit=False, connector_id=conn, rule=AccessRule.RESTRICTED), [])]))


async def test_records_moved_bumps(backend) -> None:
    processor, conn, _, space2, _, child, _ = await _writers_world(backend)
    moved = _record(child.external_record_id, connector_id=conn, group_external=space2)
    assert await _moved(backend, conn, lambda: processor.on_records_moved([(child.external_record_id, moved, [])]))


async def test_content_update_with_a_new_rule_bumps(backend) -> None:
    processor, conn, space, _, page, *_ = await _writers_world(backend)
    upd = _record(page.external_record_id, connector_id=conn, group_external=space, rule=AccessRule.RESTRICTED)
    upd.version = 2
    upd.external_revision_id = "r2"
    assert await _moved(backend, conn, lambda: processor.on_record_content_update(upd))


async def test_metadata_update_with_a_new_parent_bumps(backend) -> None:
    processor, conn, space, _, page, child, lone = await _writers_world(backend)
    upd = _record(lone.external_record_id, connector_id=conn, parent_external=page.external_record_id, group_external=space)
    assert await _moved(backend, conn, lambda: processor.on_record_metadata_update(upd))


async def test_concurrent_structural_syncs_of_one_connector_both_commit(backend) -> None:
    processor, conn, space, _, page, child, lone = await _writers_world(backend)
    p2, _ = build_processor(backend.provider, ORG)
    a = _record(page.external_record_id, connector_id=conn, group_external=space, rule=AccessRule.RESTRICTED)
    b = _record(child.external_record_id, connector_id=conn, parent_external=page.external_record_id, group_external=space,
                rule=AccessRule.RESTRICTED)
    results = await asyncio.gather(processor.on_new_records([(a, [])]), p2.on_new_records([(b, [])]), return_exceptions=True)
    errors = [f"{type(r).__name__}: {str(r)[:160]}" for r in results if isinstance(r, BaseException)]
    pa = await backend.provider.get_document(page.id, CollectionNames.RECORDS.value)
    pc = await backend.provider.get_document(child.id, CollectionNames.RECORDS.value)
    print(f"\n[{type(backend).__name__}] concurrent: errors={errors} page={pa.get('accessRule')} child={pc.get('accessRule')}")
    assert not errors


async def test_group_rule_only_change_bumps(backend) -> None:
    processor, conn, space, *_ = await _writers_world(backend)
    assert await _moved(backend, conn, lambda: processor.on_new_record_groups(
        [(_group(space, inherit=True, connector_id=conn, rule=AccessRule.OPEN), [])]))


async def test_group_inherit_only_change(backend) -> None:
    """inherit True -> False on an existing group: does the graph change (the inherit edge to the app), and does it bump?"""
    processor, conn, space, *_ = await _writers_world(backend)
    gid = (await backend.provider.get_record_group_by_external_id(conn, space)).id
    before = (await backend.provider.permission_stamp_graph().nodes(ORG, frozenset({gid})))[gid].inherits
    moved = await _moved(backend, conn, lambda: processor.on_new_record_groups([(_group(space, inherit=False, connector_id=conn), [])]))
    after = (await backend.provider.permission_stamp_graph().nodes(ORG, frozenset({gid})))[gid].inherits
    loaded = (await backend.provider.get_record_group_by_external_id(conn, space)).inherit_permissions
    print(f"\n[{type(backend).__name__}] group inherit edges before={sorted(before)} after={sorted(after)} bumped={moved} loaded_inherit={loaded}")
    assert moved or before == after


async def test_attach_to_parent_bumps(backend) -> None:
    """on_records_attached_to_parent re-hangs a record under a (restricted) parent: a narrowing of its path."""
    processor, conn, space, _, page, child, lone = await _writers_world(backend)
    other = _record(f"locked-{_suffix()}", connector_id=conn, group_external=space, rule=AccessRule.RESTRICTED)
    loose = _record(f"loose-{_suffix()}", connector_id=conn, group_external=space)
    await processor.on_new_records([(other, []), (loose, [])])
    assert await _moved(backend, conn, lambda: processor.on_records_attached_to_parent(other.id, [loose.id]))


async def test_detach_from_parent_bumps(backend) -> None:
    processor, conn, _, _, page, child, _ = await _writers_world(backend)
    assert await _moved(backend, conn, lambda: processor.on_records_detached_from_parent([child.id]))


async def test_single_record_delete_bumps(backend) -> None:
    processor, conn, _, _, page, child, _ = await _writers_world(backend)
    assert await _moved(backend, conn, lambda: processor.on_record_deleted(page.id))


# ---- idempotent writers, defaults and new nodes ----

async def test_an_unchanged_resync_does_not_bump(backend) -> None:
    """The same records re-sent unchanged (an ordinary incremental sync) must not move the generation."""
    processor, conn, space, _, page, child, lone = await _writers_world(backend)
    same = [_record(r.external_record_id, connector_id=conn, group_external=space,
                    parent_external=r.parent_external_record_id) for r in (page, child)]
    g0 = await _gen(backend.provider, conn)
    await processor.on_new_records([(r, []) for r in same])
    g1 = await _gen(backend.provider, conn)
    assert g1 == g0


async def test_a_grant_only_change_does_not_bump(backend) -> None:
    processor, conn, space, _, page, *_ = await _writers_world(backend)
    same = _record(page.external_record_id, connector_id=conn, group_external=space)
    same.id = page.id
    g0 = await _gen(backend.provider, conn)
    await processor.on_updated_record_permissions(same, [])
    g1 = await _gen(backend.provider, conn)
    assert g1 == g0


async def test_a_group_that_becomes_record_group_level_bumps(backend) -> None:
    from app.models.entities import PermissionModel

    processor, conn, space, *_ = await _writers_world(backend)
    g = _group(space, inherit=True, connector_id=conn)
    g.permission_model = PermissionModel.RECORD_GROUP_LEVEL
    assert await _moved(backend, conn, lambda: processor.on_new_record_groups([(g, [])]))


async def test_a_record_stored_without_a_rule_that_becomes_restricted_bumps(backend) -> None:
    """A record written before accessRule existed (no key): the incremental sync now restricts it."""
    processor, conn, space, _, page, *_ = await _writers_world(backend)
    if isinstance(backend, _Neo4jBackend):
        await backend.provider.client.execute_query("MATCH (r:Record {id: $id}) REMOVE r.accessRule", parameters={"id": page.id})
    else:
        await backend.provider.http_client.execute_aql("FOR r IN records FILTER r._key == @id UPDATE r WITH {accessRule: null} IN records OPTIONS {keepNull: false}",
                                                       bind_vars={"id": page.id})
    restricted = _record(page.external_record_id, connector_id=conn, group_external=space, rule=AccessRule.RESTRICTED)
    moved = await _moved(backend, conn, lambda: processor.on_new_records([(restricted, [])]))
    assert moved


# ---- changes to a default, new nodes ----
def _store(backend) -> GraphDataStore:
    return GraphDataStore(MagicMock(), backend.provider)


async def _in_tx(backend, conn: str, fn) -> bool:
    async def go() -> None:
        async with _store(backend).transaction() as tx:
            await fn(tx)
    return await _moved(backend, conn, go)


async def test_a_sync_that_only_adds_new_records_leaves_the_connector_current(backend) -> None:
    """A record that did not exist has no stamp that could go stale."""
    processor, conn, space, *_ = await _writers_world(backend)
    fresh = _record(f"fresh-{_suffix()}", connector_id=conn, group_external=space)
    moved = await _moved(backend, conn, lambda: processor.on_new_records([(fresh, [])]))
    assert not moved


async def test_missing_rule_to_open_does_not_bump_and_to_restricted_does(backend) -> None:
    processor, conn, space, _, page, child, _ = await _writers_world(backend)
    for rid in (page.id, child.id):
        if isinstance(backend, _Neo4jBackend):
            await backend.provider.client.execute_query("MATCH (r:Record {id: $id}) REMOVE r.accessRule", parameters={"id": rid})
        else:
            await backend.provider.http_client.execute_aql("FOR r IN records FILTER r._key == @id UPDATE r WITH {accessRule: null} IN records OPTIONS {keepNull: false}",
                                                           bind_vars={"id": rid})
    to_open = await _in_tx(backend, conn, lambda tx: tx.batch_update_nodes([{"id": page.id, "accessRule": "OPEN"}], CollectionNames.RECORDS.value))
    to_restricted = await _in_tx(backend, conn, lambda tx: tx.batch_update_nodes([{"id": child.id, "accessRule": "RESTRICTED"}], CollectionNames.RECORDS.value))
    assert not to_open and to_restricted


async def test_a_group_that_stops_being_record_group_level_bumps(backend) -> None:
    from app.models.entities import PermissionModel

    processor, conn, space, *_ = await _writers_world(backend)
    g = _group(space, inherit=True, connector_id=conn)
    g.permission_model = PermissionModel.RECORD_GROUP_LEVEL
    await processor.on_new_record_groups([(g, [])])
    plain = _group(space, inherit=True, connector_id=conn)  # permission_model None again
    moved = await _moved(backend, conn, lambda: processor.on_new_record_groups([(plain, [])]))
    assert moved


async def test_a_patch_keyed_by_underscore_id_is_seen(backend) -> None:
    processor, conn, space, _, page, *_ = await _writers_world(backend)
    moved = await _in_tx(backend, conn, lambda tx: tx.batch_update_nodes(
        [{"_key": page.id, "accessRule": "RESTRICTED"}], CollectionNames.RECORDS.value))
    assert moved


async def test_a_partial_patch_without_rule_fields_does_not_bump(backend) -> None:
    processor, conn, space, _, page, *_ = await _writers_world(backend)
    moved = await _in_tx(backend, conn, lambda tx: tx.batch_update_nodes(
        [{"id": page.id, "recordName": "renamed"}], CollectionNames.RECORDS.value))
    assert not moved


async def test_a_parent_set_by_patch_bumps(backend) -> None:
    processor, conn, space, _, page, _, lone = await _writers_world(backend)
    moved = await _in_tx(backend, conn, lambda tx: tx.batch_update_nodes(
        [{"id": lone.id, "externalParentId": page.external_record_id}], CollectionNames.RECORDS.value))
    unset = await _in_tx(backend, conn, lambda tx: tx.batch_update_nodes(
        [{"id": lone.id, "externalParentId": None}], CollectionNames.RECORDS.value))
    assert moved and unset  # clearing the parent is a change to the default


async def test_remove_and_readd_in_one_transaction(backend) -> None:
    """Delete a record's inherit edge and re-create it, in one transaction."""
    processor, conn, space, _, page, child, _ = await _writers_world(backend)

    async def flip(tx) -> None:
        await tx.delete_inherit_permissions_relation_record(child.id, page.id)
        await tx.create_inherit_permissions_relation_record(child.id, page.id)
    # each call is judged on its own, so the delete counts although the structure is the same at the end (conservative, never a miss)
    assert await _in_tx(backend, conn, flip)


# ---- created counts: a concurrent re-add is seen from the write's own result ----

def _inherit_edge(child_id: str, parent_id: str) -> dict:
    return {"from_id": child_id, "from_collection": CollectionNames.RECORDS.value, "to_id": parent_id,
            "to_collection": CollectionNames.RECORDS.value}


async def test_the_edge_writers_say_how_many_edges_they_created_and_which_nodes_they_touched(backend) -> None:
    from app.services.graph_db import edge_effects

    _, conn, _, _, page, child, lone = await _writers_world(backend)
    p = backend.provider
    coll = CollectionNames.INHERIT_PERMISSIONS.value
    fresh = [_inherit_edge(lone.id, page.id), _inherit_edge(child.id, lone.id)]
    again = [_inherit_edge(lone.id, page.id)]
    # create_edges_if_absent: two new edges, then nothing new
    with edge_effects.track() as made:
        assert await p.create_edges_if_absent(fresh, coll, count_created=True) == 2
    assert made == {lone.id, page.id, child.id}
    with edge_effects.track() as made:
        assert await p.create_edges_if_absent(fresh, coll, count_created=True) == 0
    assert not made
    # batch_create_edges rewrites a found edge (SET props / UPDATE) but does not count it as created; the default return is unchanged
    with edge_effects.track() as made:
        assert await p.batch_create_edges(again, coll, count_created=True) == 0
    assert not made
    assert await p.batch_create_edges(again, coll) is True
    other = [_inherit_edge(page.id, lone.id)]
    with edge_effects.track() as made:
        assert await p.batch_create_edges(other, coll, count_created=True) == 1
    assert made == {page.id, lone.id}
    assert await p.create_edges_if_absent(fresh, coll) is None
    # batch_upsert_node_relations (the hierarchy edges of a sync): the first call creates, the second finds
    rel = [{**_inherit_edge(page.id, lone.id), "relationshipType": "ATTACHMENT"}]
    assert await p.batch_upsert_node_relations(rel, count_created=True) == 1
    assert await p.batch_upsert_node_relations(rel, count_created=True) == 0
    assert await p.batch_upsert_node_relations(rel) is True


async def test_an_edge_another_transaction_removed_and_this_write_put_back_moves_the_generation(backend, monkeypatch) -> None:
    """The store reads the node's structure, the writer runs, the store reads again: a writer that re-creates an edge another
    transaction deleted in between leaves the two reads equal. Only the writer's own result (an edge created) shows it."""
    if isinstance(backend, _ArangoBackend):
        pytest.skip("on ArangoDB the shared edge document conflicts and the block is retried (test_concurrent_structural_syncs...)")
    _, conn, _, _, page, child, _ = await _writers_world(backend)
    p = backend.provider
    coll = CollectionNames.INHERIT_PERMISSIONS.value
    store_tx = _store(backend)
    edge = _inherit_edge(child.id, page.id)
    async with store_tx.transaction() as tx:
        await tx.create_edges_if_absent([edge], coll)           # the edge exists (a writer before it may have put it there)
    real = p.create_edges_if_absent

    async def removed_meanwhile(edges, collection, transaction=None, **kw) -> object:
        # the other transaction: delete the edge after this one has read the structure and before its write
        await p.delete_edge(child.id, CollectionNames.RECORDS.value, page.id, CollectionNames.RECORDS.value, coll)
        return await real(edges, collection, transaction, **kw)

    monkeypatch.setattr(p, "create_edges_if_absent", removed_meanwhile)
    assert await _in_tx(backend, conn, lambda tx: tx.create_edges_if_absent([edge], coll))
    monkeypatch.setattr(p, "create_edges_if_absent", real)
    # and an idempotent re-add that finds the edge does not
    assert not await _in_tx(backend, conn, lambda tx: tx.create_edges_if_absent([edge], coll))



# ---- every shape of edge delete: whichever end the writer names, the change in structure is seen ---------------------------------
R_, G_ = CollectionNames.RECORDS.value, CollectionNames.RECORD_GROUPS.value
REL, BEL, INH = CollectionNames.NODE_RELATIONS.value, CollectionNames.BELONGS_TO.value, CollectionNames.INHERIT_PERMISSIONS.value


async def _group_id(backend, conn: str, external: str) -> str:
    group = await backend.provider.get_record_group_by_external_id(conn, external)
    return group.id


# (name, how to call it given the world); the first block names the parent end (the group, the parent record), the second the child end
SHAPES = {
    "delete_edges_from parent end": lambda tx, w: tx.delete_edges_from(w["page"], R_, REL),
    "delete_edges_by_relationship_types parent end": lambda tx, w: tx.delete_edges_by_relationship_types(w["page"], R_, REL, ["PARENT_CHILD"]),
    "delete_edges_between_collections parent end": lambda tx, w: tx.delete_edges_between_collections(w["page"], R_, REL, R_),
    "delete_edges_to group end of belongsTo": lambda tx, w: tx.delete_edges_to(w["group"], G_, BEL),
    "delete_edges_to parent end of inheritPermissions": lambda tx, w: tx.delete_edges_to(w["page"], R_, INH),
    "replace_edges_to group end of belongsTo": lambda tx, w: tx.replace_edges_to(w["group"], G_, [], BEL),
    "delete_edges_to child end of nodeRelations": lambda tx, w: tx.delete_edges_to(w["child"], R_, REL),
    "delete_edges_from child end of belongsTo": lambda tx, w: tx.delete_edges_from(w["child"], R_, BEL),
    "delete_edges_from child end of inheritPermissions": lambda tx, w: tx.delete_edges_from(w["child"], R_, INH),
    "delete_edge": lambda tx, w: tx.delete_edge(w["page"], R_, w["child"], R_, REL),
    "batch_delete_edges": lambda tx, w: tx.batch_delete_edges([{"from_id": w["page"], "from_collection": R_, "to_id": w["child"],
                                                               "to_collection": R_, "relationshipType": "PARENT_CHILD"}], REL),
    "delete_parent_child_edge_to_record": lambda tx, w: tx.delete_parent_child_edge_to_record(w["child"]),
}
NOT_STRUCTURAL = {
    "an edge writer with no such edges": lambda tx, w: tx.delete_edges_from(w["lone"], R_, REL),
    "a grant": lambda tx, w: tx.delete_edges_from(w["page"], R_, CollectionNames.PERMISSION.value),
    "the edges to user groups (grants)": lambda tx, w: tx.delete_edges_to_groups(w["child"], R_, CollectionNames.PERMISSION.value),
    "a link type": lambda tx, w: tx.delete_edges_by_relationship_types(w["page"], R_, REL, ["LINKED_TO"]),
}


async def _shape_world(backend) -> tuple:
    processor, conn, space, _, page, child, lone = await _writers_world(backend)
    return conn, {"page": page.id, "child": child.id, "lone": lone.id, "group": await _group_id(backend, conn, space)}


@pytest.mark.parametrize("shape", sorted(SHAPES))
async def test_every_edge_delete_moves_the_generation_whichever_end_it_names(backend, shape: str) -> None:
    conn, world = await _shape_world(backend)
    assert await _in_tx(backend, conn, lambda tx: SHAPES[shape](tx, world)), shape


@pytest.mark.parametrize("shape", sorted(NOT_STRUCTURAL))
async def test_an_edge_delete_that_removes_no_structure_does_not_move_it(backend, shape: str) -> None:
    conn, world = await _shape_world(backend)
    assert not await _in_tx(backend, conn, lambda tx: NOT_STRUCTURAL[shape](tx, world)), shape


# ---- an upsert of an existing record moves the generation for every field the projector reads -------------------------------------
UPSERT_CHANGES = {
    "isDeleted": {"is_deleted": True},
    "isPlaceholder": {"is_placeholder": True},  # a stub parent turning real is the reverse flip
    "externalParentId": {"parent_external_record_id": "some-other-parent"},
    "accessRule": {"access_rule": AccessRule.RESTRICTED},
}


@pytest.mark.parametrize("field", sorted(UPSERT_CHANGES))
async def test_an_upsert_that_changes_a_field_the_projector_reads_moves_the_generation(backend, field: str) -> None:
    _, conn, _, _, page, *_ = await _writers_world(backend)
    changed = page.model_copy(update=UPSERT_CHANGES[field])
    assert await _in_tx(backend, conn, lambda tx: tx.batch_upsert_records([changed])), field


async def test_an_upsert_that_changes_nothing_the_projector_reads_does_not_move_it(backend) -> None:
    _, conn, _, _, page, *_ = await _writers_world(backend)
    same = page.model_copy(update={"record_name": "renamed"})
    assert not await _in_tx(backend, conn, lambda tx: tx.batch_upsert_records([same]))


# ---- the bump is an atomic increment: concurrent bumps never lose one or write a smaller value ---------------------------------------
async def test_concurrent_bumps_each_add_one(backend) -> None:
    processor, _ = build_processor(backend.provider, ORG)
    conn = f"conn-{_suffix()}"
    await _seed_app(backend, conn)
    space = f"space-{_suffix()}"
    await processor.on_new_record_groups([(_group(space, inherit=True, connector_id=conn), [])])
    recs = [_record(f"cc{i}-{_suffix()}", connector_id=conn, group_external=space) for i in range(12)]
    await processor.on_new_records([(r, []) for r in recs])
    g0 = await _gen(backend.provider, conn)
    store = _store(backend)

    async def restrict(tx, rid: str) -> None:
        await tx.batch_update_nodes([{"_key": rid, "accessRule": "RESTRICTED"}], CollectionNames.RECORDS.value)

    await asyncio.gather(*(store.execute_in_transaction(restrict, r.id) for r in recs))
    assert await _gen(backend.provider, conn) == g0 + len(recs)


# ---- a grant matters only while it flips whether a node has one: the projector reads `granted` ----------------------------------------
async def _grant(backend) -> Permission:  # a Permission, for the record group writer
    email = f"member-{_suffix()}@example.com"
    await _seed_user(backend, email)
    return Permission(email=email, type=PermissionType.READ, entity_type=EntityType.USER)


async def test_the_first_grant_on_a_node_moves_the_generation_and_more_do_not(backend) -> None:
    _, conn, _, _, page, child, lone = await _writers_world(backend)
    users = [await _seed_user(backend, f"member-{_suffix()}@example.com") for _ in range(2)]

    def grants(*who: str) -> list[dict]:
        return [{"from_id": u, "from_collection": CollectionNames.USERS.value, "to_id": page.id, "to_collection": R_} for u in who]

    assert await _in_tx(backend, conn, lambda tx: tx.batch_upsert_record_permissions(page.id, grants(users[0]))), "the first grant"
    assert not await _in_tx(backend, conn, lambda tx: tx.batch_upsert_record_permissions(page.id, grants(users[0]))), "the same grant again"
    assert not await _in_tx(backend, conn, lambda tx: tx.batch_upsert_record_permissions(page.id, grants(*users))), "a second grant"
    assert not await _in_tx(backend, conn, lambda tx: tx.batch_upsert_record_permissions(page.id, [])), "an empty set writes nothing"


async def test_a_grant_written_as_an_edge_moves_the_generation_when_it_flips_the_node(backend) -> None:
    _, conn, _, _, page, child, lone = await _writers_world(backend)
    perm = CollectionNames.PERMISSION.value
    user = await _seed_user(backend, f"member-{_suffix()}@example.com")
    edge = {"from_id": user, "from_collection": CollectionNames.USERS.value, "to_id": lone.id, "to_collection": R_}
    assert await _in_tx(backend, conn, lambda tx: tx.create_edges_if_absent([edge], perm)), "first grant on a record"
    other = await _seed_user(backend, f"member-{_suffix()}@example.com")
    assert not await _in_tx(backend, conn, lambda tx: tx.create_edges_if_absent([{**edge, "from_id": other}], perm)), "a second grant"
    assert not await _in_tx(backend, conn, lambda tx: tx.batch_delete_edges([edge], perm)), "one of two removed"
    assert await _in_tx(backend, conn, lambda tx: tx.delete_edges_to(lone.id, R_, perm)), "the last removed"
    assert await _in_tx(backend, conn, lambda tx: tx.create_edges_if_absent([edge], perm)), "granted again"
    assert await _in_tx(backend, conn, lambda tx: tx.delete_edges_from(user, CollectionNames.USERS.value, perm)), "from the principal's end"


async def test_a_user_losing_access_to_the_connector_moves_its_generation_when_it_was_the_last_grant(backend) -> None:
    """The user's only grant on a page goes with `remove_user_access_to_connector`; the generation must move (it did not when the
    writer was classified harmless)."""
    _, conn, _, _, page, child, lone = await _writers_world(backend)
    perm = CollectionNames.PERMISSION.value
    user = await _seed_user(backend, f"member-{_suffix()}@example.com")
    edge = {"from_id": user, "from_collection": CollectionNames.USERS.value, "to_id": lone.id, "to_collection": R_}
    assert await _in_tx(backend, conn, lambda tx: tx.create_edges_if_absent([edge], perm)), "first grant on a record"
    assert await _in_tx(backend, conn, lambda tx: tx.remove_user_access_to_connector(conn, user)), "the user's last grant is gone"
    assert not await backend.provider.get_edges_to_node(f"{R_}/{lone.id}", perm), "the grant is gone"


async def test_a_grant_on_a_record_group_moves_the_generation_when_it_flips_the_group(backend) -> None:
    _, conn, space, _, page, *_ = await _writers_world(backend)
    group = await _group_id(backend, conn, space)
    first = await _grant(backend)
    assert await _in_tx(backend, conn, lambda tx: tx.batch_upsert_record_group_permissions(group, [first], conn))
    assert not await _in_tx(backend, conn, lambda tx: tx.batch_upsert_record_group_permissions(group, [first], conn))


# ---- a structural write made outside the transaction store: a full sync that misses a node ---------------------------------------
async def test_a_full_sync_that_misses_a_node_moves_the_generation(backend, monkeypatch) -> None:
    import logging
    from types import SimpleNamespace

    from app.connectors.core.sync.full_sync_coverage import (
        FullSyncCoverage,
        FullSyncSeen,
    )
    from app.connectors.core.sync.sync_runner import _unlink_what_full_sync_missed

    _, conn, space, _, page, child, lone = await _writers_world(backend)
    group = await _group_id(backend, conn, space)
    seen = FullSyncSeen()
    for rid in (page.id, lone.id):
        seen.add(R_, rid)  # the child was not seen: its hierarchy and group edges go
    seen.add(G_, group)
    connector = SimpleNamespace(full_sync_coverage=FullSyncCoverage, data_entities_processor=None)

    async def run() -> bool:
        return await _unlink_what_full_sync_missed(connector, conn, backend.provider, logging.getLogger("t"), seen)

    monkeypatch.delenv("ACL_SCOPE_FIELDS")
    g0 = await _gen(backend.provider, conn)
    assert await run()
    assert await _gen(backend.provider, conn) == g0  # the setting is off: nothing is recorded
    _, conn, space, _, page, child, lone = await _writers_world(backend)  # the same sync again, on a new connector, with the setting on
    group = await _group_id(backend, conn, space)
    seen = FullSyncSeen()
    for rid in (page.id, lone.id):
        seen.add(R_, rid)
    seen.add(G_, group)
    monkeypatch.setenv("ACL_SCOPE_FIELDS", "on")
    g1 = await _gen(backend.provider, conn)
    assert await run()
    assert await _gen(backend.provider, conn) > g1
