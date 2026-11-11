"""Connector instances on PostgreSQL against a real database: the filtered list, the stats, the next queued duplicate, and deleting an instance with everything of it.

Both ways: a delete takes everything of the connector and nothing of another connector or org. In a caller's transaction a delete that fails part way leaves
every row and edge as it was; without one it runs in batches, and one that fails part way leaves a connector nobody can open and a retry completes it, naming
the ids of the batches that were already gone. A read that fails answers its default and answers right once the store is back.

Needs a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_POOLER=none.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

import app.services.graph_db.postgres.delete as delete_module
from app.config.constants.arangodb import Connectors

from .test_access import APP, World, _org
from .test_access import w as w
from .test_grants import failing, in_org

if TYPE_CHECKING:
    from app.core.db import Tx

pytestmark = pytest.mark.integration


async def snapshot(w: World, connector: str | None = None) -> tuple[set, set]:
    """Every node (naming the connector, when one is given) and every edge of the org as comparable tuples."""
    async def go(tx: Tx) -> tuple[set, set]:
        nodes = {(r["coll"], r["id"]) for r in await tx.fetch("SELECT coll, id FROM graph.nodes WHERE org_id = $1 AND ($2::text IS NULL OR doc->>'connectorId' = $2)", w.org, connector)}
        edges = {tuple(r) for r in await tx.fetch("SELECT coll, from_coll, from_id, to_coll, to_id, ekey FROM graph.edges WHERE org_id = $1", w.org)}
        return nodes, edges
    return await w.p._q(go, None)


async def connector(w: World, c: str, *, kind: str = "DRIVE", vrid: str = "v") -> None:
    """A connector with a record group, two records (one with a type document and a block, a label and a stage state), a role, a group, a sync point, agent knowledge and a login."""
    await w.nodes("apps", {"_key": c, "type": kind, "name": c, "vectorMembershipBackfilled": True, "vectorMembershipBackfillExhausted": False})
    await w.nodes("recordGroups", {"_key": f"{c}-rg", "connectorId": c, "name": "rg"})
    await w.nodes("records", {"_key": f"{c}-r1", "connectorId": c, "recordType": "FILE", "virtualRecordId": f"{vrid}1", "contentRev": "1"},
                  {"_key": f"{c}-r2", "connectorId": c, "recordType": "FILE", "virtualRecordId": f"{vrid}2", "contentRev": "1"})
    await w.nodes("files", {"_key": f"{c}-r1", "name": "f"}, {"_key": f"{c}-fx", "name": "x"})
    await w.nodes("mails", {"_key": f"{c}-r2", "name": "m"})           # a type document under its record's own key, with no edge
    await w.nodes("blocks", {"_key": f"{c}-b1", "recordId": f"{c}-r1"})
    await w.nodes("topics", {"_key": f"{c}-t"})
    await w.nodes("roles", {"_key": f"{c}-role", "connectorId": c})
    await w.nodes("groups", {"_key": f"{c}-grp", "connectorId": c})
    await w.nodes("syncPoints", {"_key": f"{c}-sp", "connectorId": c})
    await w.nodes("stageStates", {"_key": f"{vrid}1:1:entities", "connectorId": c, "status": "COMPLETED"})
    await w.nodes("agentKnowledge", {"_key": f"{c}-ak", "connectorId": c})
    await w.edge("belongsTo", ("recordGroups", f"{c}-rg"), ("apps", c))
    for r in ("r1", "r2"):
        await w.edge("belongsTo", ("records", f"{c}-{r}"), ("recordGroups", f"{c}-rg"))
    await w.edge("isOfType", ("records", f"{c}-r1"), ("files", f"{c}-r1"))
    await w.edge("isOfType", ("records", f"{c}-r2"), ("files", f"{c}-fx"))
    await w.edge("belongsToTopic", ("records", f"{c}-r1"), ("topics", f"{c}-t"))
    await w.edge("permission", ("users", "u"), ("recordGroups", f"{c}-rg"), type="USER", role="READER")
    await w.edge("permission", ("users", "u"), ("roles", f"{c}-role"), type="USER", role="READER")
    await w.edge("authenticatedAs", ("users", "u"), ("apps", c), connectorId=c)
    await w.edge("agentHasKnowledge", ("agentInstances", "ag"), ("agentKnowledge", f"{c}-ak"))


async def test_a_deleted_instance_takes_all_of_it_and_nothing_of_another_connector(w: World) -> None:
    await w.nodes("agentInstances", {"_key": "ag"})
    await w.nodes("topics", {"_key": "shared-topic"})
    await connector(w, "c1")
    await connector(w, "c2", vrid="x")
    await w.nodes("records", {"_key": "c2-r3", "connectorId": "c2", "recordType": "FILE", "virtualRecordId": "x3"})
    await w.edge("belongsToTopic", ("records", "c2-r3"), ("topics", "c1-t"))      # another connector's record keeps the label it shares
    await w.edge("belongsToTopic", ("records", "c1-r2"), ("topics", "shared-topic"))
    got = await w.p.delete_connector_instance("c1", w.org)
    assert got["success"] is True and got["deleted_records_count"] == 2 and got["deleted_record_groups_count"] == 1 and got["deleted_roles_count"] == 1
    assert got["deleted_groups_count"] == 1 and got["deleted_blocks_count"] == 1 and got["deleted_isoftype_targets_count"] == 3
    assert got["record_group_ids"] == ["c1-rg"] and got["connector_id"] == "c1" and got["connector_name"] == "DRIVE"
    assert got["vector_membership_backfilled"] is True and got["vector_membership_backfill_exhausted"] is False and got["virtual_record_ids"] == []
    nodes, edges = await snapshot(w)
    assert not {n for n in nodes if n[1].startswith("c1-") and n != ("topics", "c1-t")} and ("apps", "c1") not in nodes and ("stageStates", "v1:1:entities") not in nodes
    assert ("topics", "c1-t") in nodes                                  # a record of another connector still links it
    assert ("topics", "shared-topic") not in nodes and got["deleted_orphan_taxonomy_count"] == 1      # only c1 linked it
    assert not {n for n in nodes if n[0] in ("files", "mails") and n[1].startswith("c1-")}
    assert not [e for e in edges if ("c1-" in e[2] + e[4] or e[4] == "c1") and e[4] != "c1-t"]
    n2 = {x for x in nodes if x[1].startswith("c2")}
    assert {("apps", "c2"), ("recordGroups", "c2-rg"), ("records", "c2-r1"), ("records", "c2-r2"), ("files", "c2-r1"), ("files", "c2-fx"), ("mails", "c2-r2"), ("blocks", "c2-b1"), ("roles", "c2-role"),
            ("groups", "c2-grp"), ("syncPoints", "c2-sp"), ("agentKnowledge", "c2-ak")} <= n2
    assert any(e[0] == "authenticatedAs" and e[4] == "c2" for e in edges)
    assert any(e[0] == "isOfType" and e[2] == "c2-r1" for e in edges) and any(e[0] == "belongsToTopic" and e[2] == "c2-r1" for e in edges)
    assert await w.p.delete_connector_instance("c1", w.org) == {"success": False, "error": "Connector instance c1 not found"}


async def test_the_late_write_pass_removes_what_indexing_wrote_after_the_delete_and_nothing_else(w: World) -> None:
    """A stage job and a duplicate code file can still write for a connector after its delete committed."""
    await connector(w, "c1")
    await connector(w, "c2", vrid="x")
    assert (await w.p.delete_connector_instance("c1", w.org))["success"] is True
    await w.nodes("blocks", {"_key": "c1-late-b", "connectorId": "c1", "recordId": "c1-r1"})
    await w.nodes("stageStates", {"_key": "v1:2:entities", "connectorId": "c1", "status": "IN_PROGRESS"})
    await w.nodes("blocks", {"_key": "c2-b2", "connectorId": "c2", "recordId": "c2-r1"})
    await w.edge("recordLinks", ("blocks", "c2-b2"), ("blocks", "c1-late-b"), relationshipType="CALLS")

    async def dangling(tx: Tx) -> None:
        """A code link whose statement saw its blocks commits after they went: the writer does not lock the ends, and no foreign key ties them."""
        for group, end in (("c1-rg", "c1-gone"), ("c2-rg", "c2-gone")):
            await tx.execute("""INSERT INTO graph.edges (org_id, coll, from_coll, from_id, to_coll, to_id, doc)
                VALUES ($1, 'nodeRelations', 'blocks', $2, 'blocks', $3, jsonb_build_object('relationshipType', 'CALLS', 'recordGroupId', $4::text))""",
                             w.org, f"{end}-a", f"{end}-b", group)
    await w.p._q(dangling, None, write=True)

    assert await w.p.sweep_deleted_connector_writes("c1", ["c1-rg"]) == (3, 0)
    nodes, edges = await snapshot(w)
    assert ("blocks", "c1-late-b") not in nodes and ("stageStates", "v1:2:entities") not in nodes
    assert ("blocks", "c2-b2") in nodes and ("stageStates", "x1:1:entities") in nodes
    assert not [e for e in edges if "c1-late-b" in (e[2], e[4])]                 # the block goes with every edge around it
    assert not [e for e in edges if e[2] == "c1-gone-a"] and [e for e in edges if e[2] == "c2-gone-a"]      # only the deleted connector's groups
    assert await w.p.sweep_deleted_connector_writes("c1", ["c1-rg"]) == (0, 0)


async def test_the_scopes_of_a_deleted_connector_are_dropped_and_those_of_a_failed_delete_are_kept(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    await connector(w, "c1")
    forgotten: list[str] = []

    async def forget(connector_id: str) -> None:
        forgotten.append(connector_id)
    monkeypatch.setattr(w.p, "kh_scope_forget", forget)
    assert (await w.p.delete_connector_instance("zzz", w.org))["success"] is False and forgotten == []
    assert (await w.p.delete_connector_instance("c1", w.org))["success"] is True and forgotten == ["c1"]


async def test_the_virtual_record_ids_are_named_only_when_the_app_cannot_be_cleaned_by_membership(w: World) -> None:
    await connector(w, "c1")
    await w.p.update_node("c1", "apps", {"vectorMembershipBackfilled": False})
    assert sorted((await w.p.delete_connector_instance("c1", w.org))["virtual_record_ids"]) == ["v1", "v2"]


async def test_another_orgs_instance_is_not_there_and_not_deleted(w: World) -> None:
    await connector(w, "c1")
    other = _org()
    with in_org(other):                                                        # the same keys in another org stay when this org's instance goes
        await w.p.batch_upsert_nodes([{"_key": "c1", "orgId": other, "type": "DRIVE"}], "apps")
        await w.p.batch_upsert_nodes([{"_key": "c1-r1", "orgId": other, "connectorId": "c1"}], "records")
    assert (await w.p.delete_connector_instance("c1", w.org))["success"] is True
    with in_org(other):
        assert await w.p.check_collection_has_document("apps", "c1") and await w.p.check_collection_has_document("records", "c1-r1")
        await w.p.delete_connector_instance("c1", other)
    await connector(w, "c1")
    assert await w.p.delete_connector_instance("c1", other) == {"success": False, "error": "Connector instance c1 not found"}
    assert ("apps", "c1") in (await snapshot(w))[0]
    with in_org(other):
        assert (await w.p.delete_connector_instance("c1", other))["error"] == "Connector instance c1 not found"
    assert ("apps", "c1") in (await snapshot(w))[0]


async def test_a_delete_that_fails_at_the_last_step_leaves_the_app_and_a_retry_completes(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    await connector(w, "c1")
    real = delete_module._remove_nodes

    async def boom(tx: Tx, org: str, coll: str, keys: list[str]) -> int:
        if coll == "apps":
            raise RuntimeError("store down")
        return await real(tx, org, coll, keys)
    monkeypatch.setattr(delete_module, "_remove_nodes", boom)
    got = await w.p.delete_connector_instance("c1", w.org)
    assert got["success"] is False and got["partial"] is True and got["error"].startswith("Failed to delete connector instance:")
    nodes = (await snapshot(w))[0]
    assert ("apps", "c1") in nodes and ("records", "c1-r1") not in nodes and ("recordGroups", "c1-rg") not in nodes      # the batches are committed, the app waits
    monkeypatch.setattr(delete_module, "_remove_nodes", real)
    again = await w.p.delete_connector_instance("c1", w.org)
    assert again["success"] is True and again["record_group_ids"] == ["c1-rg"]           # named by the first attempt's stash
    nodes, edges = await snapshot(w)
    assert ("apps", "c1") not in nodes and not [n for n in nodes if n[0] == "connectorDeleteHandles" or n[1].startswith("c1-")]


async def test_a_retry_that_cannot_close_the_connector_again_stays_partial(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    """The first attempt has removed the grants and the records; a lock timeout on the app row at the start of the retry must not bring the connector back."""
    await connector(w, "c1")
    real_nodes, real_close = delete_module._remove_nodes, delete_module._close_connector

    async def boom(tx: Tx, org: str, coll: str, keys: list[str]) -> int:
        if coll == "apps":
            raise RuntimeError("store down")
        return await real_nodes(tx, org, coll, keys)

    async def locked(tx: Tx, org: str, connector_id: str) -> None:
        raise RuntimeError("canceling statement due to lock timeout")
    monkeypatch.setattr(delete_module, "_remove_nodes", boom)
    assert (await w.p.delete_connector_instance("c1", w.org))["partial"] is True
    monkeypatch.setattr(delete_module, "_close_connector", locked)
    got = await w.p.delete_connector_instance("c1", w.org)
    assert got["success"] is False and got["partial"] is True and "lock timeout" in got["error"]
    monkeypatch.setattr(delete_module, "_remove_nodes", real_nodes)
    monkeypatch.setattr(delete_module, "_close_connector", real_close)
    assert (await w.p.delete_connector_instance("c1", w.org))["success"] is True


async def test_a_delete_in_a_caller_transaction_that_fails_part_way_changes_nothing_and_a_retry_completes(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    await connector(w, "c1")
    before = await snapshot(w)
    real = delete_module._remove_nodes

    async def boom(tx: Tx, org: str, coll: str, keys: list[str]) -> int:
        if coll == "apps":
            raise RuntimeError("store down")
        return await real(tx, org, coll, keys)
    monkeypatch.setattr(delete_module, "_remove_nodes", boom)
    t = await w.p.begin_transaction([], [])
    got = await w.p.delete_connector_instance("c1", w.org, t)
    assert got["success"] is False and got["error"].startswith("Failed to delete connector instance:") and not got.get("partial")
    await w.p.commit_transaction(t)
    assert await snapshot(w) == before                         # rolled back to the savepoint: every row and edge is still there
    monkeypatch.setattr(delete_module, "_remove_nodes", real)
    assert (await w.p.delete_connector_instance("c1", w.org))["success"] is True
    assert ("apps", "c1") not in (await snapshot(w))[0]


async def test_a_delete_that_cannot_remove_everything_is_refused_and_a_retry_completes(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    await connector(w, "c1")
    real = delete_module._remove_records

    async def nothing(tx: Tx, org: str, ids: list[str]) -> int:
        return 0
    monkeypatch.setattr(delete_module, "_remove_records", nothing)
    got = await w.p.delete_connector_instance("c1", w.org)
    assert got["success"] is False and "remain" in got["error"]
    assert {("apps", "c1"), ("records", "c1-r1"), ("records", "c1-r2")} <= (await snapshot(w))[0]
    monkeypatch.setattr(delete_module, "_remove_records", real)
    assert (await w.p.delete_connector_instance("c1", w.org))["success"] is True
    assert not [n for n in (await snapshot(w))[0] if n[1].startswith("c1-")]


async def test_a_delete_in_a_caller_transaction_that_cannot_remove_everything_changes_nothing(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    await connector(w, "c1")
    before = await snapshot(w)

    async def nothing(tx: Tx, org: str, ids: list[str]) -> int:
        return 0
    monkeypatch.setattr(delete_module, "_remove_records", nothing)
    t = await w.p.begin_transaction([], [])
    got = await w.p.delete_connector_instance("c1", w.org, t)
    assert got["success"] is False and "remain" in got["error"]
    await w.p.rollback_transaction(t)
    assert await snapshot(w) == before


async def test_a_refused_delete_in_a_caller_transaction_leaves_nothing_for_its_commit(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    """A delete that fails part way inside the caller's transaction rolls back to its savepoint: the caller's commit keeps none of it."""
    await connector(w, "c1")
    before = await snapshot(w)

    async def nothing(tx: Tx, org: str, ids: list[str]) -> int:
        return 0
    monkeypatch.setattr(delete_module, "_remove_records", nothing)
    t = await w.p.begin_transaction([], [])
    got = await w.p.delete_connector_instance("c1", w.org, t)
    assert got["success"] is False and "remain" in got["error"]
    await w.p.commit_transaction(t)
    assert await snapshot(w) == before


async def test_a_label_a_merge_points_at_or_from_is_left_for_the_redirect(w: World) -> None:
    await connector(w, "c1")
    await w.nodes("topics", {"_key": "loser", "mergedInto": "winner"}, {"_key": "winner"}, {"_key": "plain"})
    for t in ("loser", "winner", "plain"):
        await w.edge("belongsToTopic", ("records", "c1-r2"), ("topics", t))
    got = await w.p.delete_connector_instance("c1", w.org)
    nodes = (await snapshot(w))[0]
    assert ("topics", "loser") in nodes and ("topics", "winner") in nodes and ("topics", "plain") not in nodes and ("topics", "c1-t") not in nodes
    assert got["deleted_orphan_taxonomy_count"] == 2


async def test_a_record_written_while_the_delete_ran_is_taken_with_it(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    await connector(w, "c1")
    real = delete_module._remove_records
    calls = 0

    async def late(tx: Tx, org: str, ids: list[str]) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            await tx.execute("INSERT INTO graph.nodes (org_id, coll, id, doc) VALUES ($1, 'records', 'c1-late', jsonb_build_object('id', 'c1-late', 'connectorId', 'c1'))", org)
        return await real(tx, org, ids)
    monkeypatch.setattr(delete_module, "_remove_records", late)
    assert (await w.p.delete_connector_instance("c1", w.org))["deleted_records_count"] == 3
    assert ("records", "c1-late") not in (await snapshot(w))[0]


async def test_a_record_written_during_the_delete_of_an_app_without_membership_cleanup_names_its_vector(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    await connector(w, "c1")
    await w.p.update_node("c1", "apps", {"vectorMembershipBackfilled": False})
    real = delete_module._remove_records
    calls = 0

    async def late(tx: Tx, org: str, ids: list[str]) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            await tx.execute("INSERT INTO graph.nodes (org_id, coll, id, doc) VALUES ($1, 'records', 'c1-late', jsonb_build_object('id', 'c1-late', 'connectorId', 'c1', 'virtualRecordId', 'vlate'))", org)
        return await real(tx, org, ids)
    monkeypatch.setattr(delete_module, "_remove_records", late)
    got = await w.p.delete_connector_instance("c1", w.org)
    assert got["success"] is True and sorted(got["virtual_record_ids"]) == ["v1", "v2", "vlate"]


async def test_a_label_linked_while_the_delete_ran_is_kept_with_its_link(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    await connector(w, "c1")
    await w.nodes("records", {"_key": "x1", "connectorId": "c2"})
    real = delete_module._lock_labels

    async def link_after_lock(tx: Tx, org: str, coll: str, keys: list[str]) -> None:
        await real(tx, org, coll, keys)
        # what a writer that committed after the labels were read would have left (the next statement sees a committed row as it sees this one)
        await tx.execute("INSERT INTO graph.edges (org_id, coll, from_coll, from_id, to_coll, to_id, ekey, doc) VALUES ($1, 'belongsToTopic', 'records', 'x1', 'topics', 'c1-t', '', '{}'::jsonb)", org)
    monkeypatch.setattr(delete_module, "_lock_labels", link_after_lock)
    got = await w.p.delete_connector_instance("c1", w.org)
    nodes, edges = await snapshot(w)
    assert got["success"] is True and ("topics", "c1-t") in nodes and ("belongsToTopic", "records", "x1", "topics", "c1-t", "") in edges and got["deleted_orphan_taxonomy_count"] == 0


async def test_blocks_that_name_the_connector_go_even_when_no_record_owns_them(w: World) -> None:
    await connector(w, "c1")
    await connector(w, "c2", vrid="x")
    await w.nodes("blocks", {"_key": "orphan", "recordId": "gone-record", "connectorId": "c1"}, {"_key": "orphan2", "recordId": "gone-record", "connectorId": "c2"})
    got = await w.p.delete_connector_instance("c1", w.org)
    nodes = (await snapshot(w))[0]
    assert ("blocks", "orphan") not in nodes and ("blocks", "orphan2") in nodes and got["deleted_blocks_count"] == 2          # the block of a record, and the orphan


async def test_a_delete_that_leaves_a_block_of_the_connector_is_refused_and_a_retry_completes(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    await connector(w, "c1")
    await w.nodes("blocks", {"_key": "orphan", "recordId": "gone-record", "connectorId": "c1"})
    real = delete_module._remove_nodes

    async def keep_blocks(tx: Tx, org: str, coll: str, keys: list[str]) -> int:
        return 0 if coll == "blocks" else await real(tx, org, coll, keys)
    monkeypatch.setattr(delete_module, "_remove_nodes", keep_blocks)
    got = await w.p.delete_connector_instance("c1", w.org)
    assert got["success"] is False and got["partial"] is True and "remain in blocks" in got["error"] and ("apps", "c1") in (await snapshot(w))[0]
    monkeypatch.setattr(delete_module, "_remove_nodes", real)
    assert (await w.p.delete_connector_instance("c1", w.org))["success"] is True
    assert not [n for n in (await snapshot(w))[0] if n[1].startswith("c1") or n[1] == "orphan"]


async def test_a_delete_in_a_caller_transaction_that_leaves_a_block_is_refused_and_changes_nothing(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    await connector(w, "c1")
    await w.nodes("blocks", {"_key": "orphan", "recordId": "gone-record", "connectorId": "c1"})
    before = await snapshot(w)
    real = delete_module._remove_nodes

    async def keep_blocks(tx: Tx, org: str, coll: str, keys: list[str]) -> int:
        return 0 if coll == "blocks" else await real(tx, org, coll, keys)
    monkeypatch.setattr(delete_module, "_remove_nodes", keep_blocks)
    t = await w.p.begin_transaction([], [])
    got = await w.p.delete_connector_instance("c1", w.org, t)
    assert got["success"] is False and "remain" in got["error"]
    await w.p.commit_transaction(t)
    assert await snapshot(w) == before


async def test_the_instances_list_is_scoped_filtered_literal_and_newest_first(w: World) -> None:
    await w.nodes("apps", {"_key": "a", "name": "Alpha_One", "type": "DRIVE", "appGroup": "Google", "scope": "team", "createdBy": "boss", "createdAtTimestamp": 1},
                  {"_key": "b", "name": "Beta", "type": "SLACK", "appGroup": "Chat", "scope": "personal", "createdBy": "boss", "createdAtTimestamp": 3},
                  {"_key": "c", "name": "100% Gamma", "type": "JIRA", "appGroup": "Atlassian", "scope": "personal", "createdBy": "ann", "createdAtTimestamp": 2},
                  {"_key": "d", "name": "Delta", "type": "X", "scope": "team", "createdBy": "ann", "createdAtTimestamp": "soon"})
    p = w.p
    async def ids(**kw: str | bool | int | None) -> tuple[list[str], int]:
        got, total = await p.get_connector_instances_with_filters("apps", **kw)
        return [d["_key"] for d in got], total
    assert await ids(user_id="boss", is_admin=True) == (["b", "a", "d"], 3)
    assert (await ids(user_id="boss"))[0] == ["b", "a"] and (await ids(user_id="ann"))[0] == ["c", "d"]       # not an admin: only their own
    assert (await ids(user_id="ann", is_admin=True))[0] == ["c", "a", "d"]                                    # an admin: the team's and their own
    assert await ids(user_id=None, is_admin=True) == (["a", "d"], 2)                                         # an admin with no user: the team's
    assert await ids(user_id=None) == ([], 0) and await ids(user_id="nobody") == ([], 0)
    assert (await ids(user_id="ann", scope="personal"))[0] == ["c"]
    assert (await ids(user_id="boss", search="ALPHA"))[0] == ["a"] and (await ids(user_id="boss", search="slack"))[0] == ["b"] and (await ids(user_id="boss", search="chat"))[0] == ["b"]
    assert (await ids(user_id="ann", search="100%"))[0] == ["c"] and (await ids(user_id="ann", search="%"))[0] == ["c"]       # a percent sign is a character
    assert (await ids(user_id="boss", is_admin=True, search="a_o"))[0] == ["a"] and await ids(user_id="boss", is_admin=True, search="a.o") == ([], 0)
    assert await ids(user_id="boss", is_admin=True, search="alp_a") == ([], 0)            # an underscore is a character, not any one
    assert await ids(user_id="ann", search="0\\%") == ([], 0) and await ids(user_id="boss", is_admin=True, search="\\a") == ([], 0)                                # nor is a backslash an escape
    assert await ids(user_id="boss", is_admin=True, page=2, limit=2) == (["d"], 3) and await ids(user_id="boss", is_admin=True, page=5, limit=2) == ([], 3)
    assert await ids(user_id="boss", is_admin=True, limit=1) == (["b"], 3)
    with in_org(_org()):
        assert await ids(user_id="boss", is_admin=True) == ([], 0)


async def test_a_failed_instances_list_is_empty_and_comes_back(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    await w.nodes("apps", {"_key": "a", "name": "A", "scope": "team", "createdBy": "boss", "createdAtTimestamp": 1})
    real = w.p._q
    monkeypatch.setattr(w.p, "_q", failing)
    assert await w.p.get_connector_instances_with_filters("apps", user_id="boss", is_admin=True) == ([], 0)
    monkeypatch.setattr(w.p, "_q", real)
    assert (await w.p.get_connector_instances_with_filters("apps", user_id="boss", is_admin=True))[1] == 1


async def test_the_stats_count_the_live_records_of_the_connector_once(w: World) -> None:
    await w.nodes("recordGroups", {"_key": "top"}, {"_key": "mid"}, {"_key": "low"}, {"_key": "cut"})
    await w.edge("belongsTo", ("recordGroups", "top"), ("apps", APP))
    await w.edge("belongsTo", ("recordGroups", "mid"), ("recordGroups", "top"))
    await w.edge("belongsTo", ("recordGroups", "low"), ("recordGroups", "mid"))
    await w.edge("belongsTo", ("recordGroups", "cut"), ("recordGroups", "gone"))                       # not reached from the app
    base = {"recordType": "FILE", "indexingStatus": "COMPLETED", "extractionStatus": "NOT_STARTED"}
    await w.nodes("records", {"_key": "ok", **base, "virtualRecordId": "v", "contentRev": "1"}, {"_key": "twice", **base}, {"_key": "deep", **base},
                  {"_key": "in", **base, "isInternal": True}, {"_key": "ph", **base, "isPlaceholder": True}, {"_key": "del", **base, "isDeleted": True},
                  {"_key": "folder", **base, "mimeType": "application/vnd.google-apps.folder"}, {"_key": "stray", **base})
    await w.nodes("stageStates", {"_key": "v:1:entities", "status": "COMPLETED"})
    for r, g in (("ok", "top"), ("twice", "top"), ("twice", "mid"), ("deep", "low"), ("in", "top"), ("ph", "top"), ("del", "top"), ("folder", "top"), ("stray", "cut")):
        await w.edge("belongsTo", ("records", r), ("recordGroups", g))
    for i in range(1, 13):      # a chain of groups up to the app: the first ten hops count, the ones past them do not
        await w.nodes("recordGroups", {"_key": f"d{i}"})
        await w.edge("belongsTo", ("recordGroups", f"d{i}"), ("apps", APP) if i == 1 else ("recordGroups", f"d{i - 1}"))
    await w.nodes("records", {"_key": "at10", **base}, {"_key": "at11", **base})
    await w.edge("belongsTo", ("records", "at10"), ("recordGroups", "d10"))
    await w.edge("belongsTo", ("records", "at11"), ("recordGroups", "d11"))
    got = await w.p.get_connector_stats(w.org, APP)
    assert got["success"] is True and got["data"]["origin"] == "CONNECTOR" and got["data"]["stats"]["total"] == 4          # ok, twice, deep, the one ten hops up
    assert got["data"]["stats"]["entitiesStatus"]["COMPLETED"] == 1                                    # only ok has a stage state on its revision
    assert (await w.p.get_connector_stats(_org(), APP))["data"]["stats"]["total"] == 0                           # another org's id sees nothing
    await w.nodes("apps", {"_key": "kb", "type": Connectors.KNOWLEDGE_BASE.value})
    await w.nodes("records", {"_key": "k1", **base}, {"_key": "k2", **base, "isDeleted": True})
    for r in ("k1", "k2", "ok"):
        await w.edge("belongsTo", ("records", r), ("apps", "kb"))
    kb = await w.p.get_connector_stats(w.org, "kb")
    assert kb["data"]["origin"] == "COLLECTION" and kb["data"]["stats"]["total"] == 2
    real = w.p._q
    w.p._q = failing
    try:
        assert (await w.p.get_connector_stats(w.org, APP)) == {"success": False, "message": "store down", "data": None}
    finally:
        w.p._q = real
    assert (await w.p.get_connector_stats(w.org, APP))["success"] is True


async def test_the_next_queued_duplicate_is_live_queued_same_md5_and_size_in_the_same_org(w: World) -> None:
    same = {"md5Checksum": "m", "orgId": w.org}
    await w.nodes("records", {"_key": "ref", **same, "sizeInBytes": 10, "indexingStatus": "COMPLETED"},
                  {"_key": "qsize", **same, "sizeInBytes": 11, "indexingStatus": "QUEUED"}, {"_key": "q-del", **same, "sizeInBytes": 10, "indexingStatus": "QUEUED", "isDeleted": True},
                  {"_key": "done", **same, "sizeInBytes": 10, "indexingStatus": "COMPLETED"}, {"_key": "q-md5", "md5Checksum": "n", "orgId": w.org, "sizeInBytes": 10, "indexingStatus": "QUEUED"},
                  {"_key": "q-org", "md5Checksum": "m", "orgId": "elsewhere", "sizeInBytes": 10, "indexingStatus": "QUEUED"},
                  {"_key": "nomd5", "orgId": w.org, "indexingStatus": "COMPLETED"}, {"_key": "noorg", "md5Checksum": "m", "indexingStatus": "COMPLETED"})
    assert await w.p.find_next_queued_duplicate("ref") is None
    await w.p.update_node("ref", "records", {"indexingStatus": "QUEUED"})
    assert await w.p.find_next_queued_duplicate("ref") is None                  # a queued reference is not its own duplicate
    await w.p.update_node("ref", "records", {"indexingStatus": "COMPLETED"})
    await w.nodes("records", {"_key": "q2", **same, "sizeInBytes": 10, "indexingStatus": "QUEUED"}, {"_key": "q1", **same, "sizeInBytes": 10, "indexingStatus": "QUEUED"})
    assert (await w.p.find_next_queued_duplicate("ref"))["_key"] == "q1"
    await w.p.update_node("ref", "records", {"sizeInBytes": None})
    assert (await w.p.find_next_queued_duplicate("ref"))["_key"] == "q1"
    await w.p.update_node("q1", "records", {"isDeleted": True})
    await w.p.update_node("q2", "records", {"isDeleted": True})
    assert (await w.p.find_next_queued_duplicate("ref"))["_key"] == "qsize"                              # no size on the reference: any size
    await w.p.update_node("noorg", "records", {"orgId": None})                  # the writer stamps the org on every node; a record without one finds nothing
    assert await w.p.find_next_queued_duplicate("nomd5") is None and await w.p.find_next_queued_duplicate("noorg") is None and await w.p.find_next_queued_duplicate("missing") is None
    real = w.p._q
    w.p._q = failing
    try:
        assert await w.p.find_next_queued_duplicate("ref") is None
        with pytest.raises(RuntimeError):
            await w.p.find_next_queued_duplicate("ref", raise_on_error=True)
    finally:
        w.p._q = real
    assert (await w.p.find_next_queued_duplicate("ref"))["_key"] == "qsize"


# ---- a delete in batches that is interrupted, and run again ----------------------------------------------------------------------

async def open_world(w: World) -> None:
    """A connector the user ``u`` (and a group ``u`` is in) opens: records under record groups, one record granted to a second user, labels and no membership cleanup."""
    await w.nodes("users", {"_key": "u", "userId": "uid-u"}, {"_key": "u2", "userId": "uid-u2"})
    await w.nodes("apps", {"_key": "c1", "type": "DRIVE", "vectorMembershipBackfilled": False})
    await w.nodes("recordGroups", {"_key": "c1-g1", "connectorId": "c1"}, {"_key": "c1-g2", "connectorId": "c1"}, {"_key": "c1-g3", "connectorId": "c1"})
    await w.nodes("topics", {"_key": "only-c1"}, {"_key": "shared"})
    await w.nodes("records", {"_key": "other-r", "connectorId": "c2", "virtualRecordId": "vo"})
    for g in ("c1-g1", "c1-g2", "c1-g3"):
        await w.child(("apps", "c1"), ("recordGroups", g))
    for i in range(7):
        await w.nodes("records", {"_key": f"c1-r{i}", "connectorId": "c1", "recordType": "FILE", "virtualRecordId": f"v{i}", "indexingStatus": "COMPLETED", "origin": "CONNECTOR"})
        await w.child(("recordGroups", f"c1-g{1 + i % 3}"), ("records", f"c1-r{i}"))
        await w.edge("belongsToTopic", ("records", f"c1-r{i}"), ("topics", "only-c1" if i < 6 else "shared"))
    await w.edge("belongsToTopic", ("records", "other-r"), ("topics", "shared"))
    await w.nodes("stageStates", *({"_key": f"c1-ss{i}", "connectorId": "c1", "status": "COMPLETED"} for i in range(5)),      # none that names a virtual record, and one that does
                  {"_key": "c1-ssv", "connectorId": "c1", "virtualRecordId": "v0", "status": "COMPLETED"}, {"_key": "other-ss", "connectorId": "c2", "virtualRecordId": "vo"})
    for who in ("u", "u2"):
        await w.edge("permission", ("users", who), ("apps", "c1"), type="USER", role="READER")
    await w.edge("permission", ("users", "u2"), ("records", "c1-r3"), type="USER", role="READER")


async def can_open(w: World, who: str) -> set[str]:
    got = await w.p.check_access(who, w.org, node_ids=[*(f"c1-r{i}" for i in range(7)), "c1-g1", "c1-g2", "c1-g3", "c1"])
    return set(got.node_ids)


@pytest.mark.parametrize("fail_at", ["second_record_batch", "record_groups", "last_unit"])
async def test_an_interrupted_delete_in_batches_opens_nothing_and_a_rerun_finishes_it_naming_every_id(w: World, monkeypatch: pytest.MonkeyPatch, fail_at: str) -> None:
    await open_world(w)
    monkeypatch.setattr(delete_module, "_CHUNK", 2)
    assert len(await can_open(w, "u")) == 11 and len(await w.p.get_accessible_virtual_record_ids("uid-u", w.org)) == 7        # open to the user before the delete
    assert "c1-r3" in await can_open(w, "u2")
    real_records, real_nodes, real_finish = delete_module._remove_records, delete_module._remove_nodes, delete_module._finish_connector
    calls = {"records": 0}

    async def records(tx: Tx, org: str, ids: list[str]) -> int:
        calls["records"] += 1
        if fail_at == "second_record_batch" and calls["records"] == 2:
            raise RuntimeError("worker killed")
        return await real_records(tx, org, ids)

    async def nodes(tx: Tx, org: str, coll: str, keys: list[str]) -> int:
        if fail_at == "record_groups" and coll == "recordGroups" and keys == ["c1-g3"]:
            raise RuntimeError("worker killed")
        return await real_nodes(tx, org, coll, keys)

    async def finish(*a: object, **k: object) -> int:
        raise RuntimeError("worker killed")
    monkeypatch.setattr(delete_module, "_remove_records", records)
    monkeypatch.setattr(delete_module, "_remove_nodes", nodes)
    if fail_at == "last_unit":
        monkeypatch.setattr(delete_module, "_finish_connector", finish)
    got = await w.p.delete_connector_instance("c1", w.org)
    assert got["success"] is False and "worker killed" in got["error"]
    left = (await snapshot(w))[0]
    assert ("apps", "c1") in left and ("records", "c1-r0") not in left                  # it stopped part way: the connector is still there and some of it is gone
    assert ("records", "c1-r6") in left or fail_at != "second_record_batch"
    # what is left opens to nobody: not the user the connector was open to, not the one with a grant on a record, and the search scope is empty
    assert await can_open(w, "u") == set() and await can_open(w, "u2") == set()
    assert await w.p.get_accessible_virtual_record_ids("uid-u", w.org) == {} and await w.p.get_accessible_virtual_record_ids("uid-u2", w.org) == {}
    monkeypatch.setattr(delete_module, "_remove_records", real_records)
    monkeypatch.setattr(delete_module, "_remove_nodes", real_nodes)
    monkeypatch.setattr(delete_module, "_finish_connector", real_finish)
    done = await w.p.delete_connector_instance("c1", w.org)
    assert done["success"] is True
    assert done["virtual_record_ids"] == [f"v{i}" for i in range(7)] and done["record_group_ids"] == ["c1-g1", "c1-g2", "c1-g3"]      # also those removed before the interruption
    nodes_left, edges_left = await snapshot(w)
    assert not [n for n in nodes_left if n[1].startswith("c1") or n[0] == "connectorDeleteHandles"] and ("topics", "only-c1") not in nodes_left
    assert {("topics", "shared"), ("records", "other-r")} <= nodes_left and done["deleted_orphan_taxonomy_count"] == 1
    assert not [e for e in edges_left if "c1" in e[2] + e[4]]
    assert (await w.p.delete_connector_instance("c1", w.org))["success"] is False


async def test_a_connector_of_many_records_goes_in_batches_of_the_chunk_size(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    await open_world(w)
    monkeypatch.setattr(delete_module, "_CHUNK", 2)
    real = delete_module._remove_records
    sizes: list[int] = []

    async def spy(tx: Tx, org: str, ids: list[str]) -> int:
        sizes.append(len(ids))
        return await real(tx, org, ids)
    monkeypatch.setattr(delete_module, "_remove_records", spy)
    got = await w.p.delete_connector_instance("c1", w.org)
    assert got["deleted_records_count"] == 7 and got["deleted_record_groups_count"] == 3 and sizes == [2, 2, 2, 1]


async def test_two_deletes_of_one_connector_at_once_leave_one_answer_that_names_every_id(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio
    await open_world(w)
    monkeypatch.setattr(delete_module, "_CHUNK", 2)
    for _ in range(4):
        got = await asyncio.gather(w.p.delete_connector_instance("c1", w.org), w.p.delete_connector_instance("c1", w.org))
        won = [g for g in got if g["success"]]
        assert len(won) == 1, got
        assert won[0]["virtual_record_ids"] == [f"v{i}" for i in range(7)] and won[0]["record_group_ids"] == ["c1-g1", "c1-g2", "c1-g3"]      # also what the other run removed
        assert won[0]["deleted_orphan_taxonomy_count"] == 1
        nodes, _ = await snapshot(w)
        assert not [n for n in nodes if n[1].startswith("c1") or n[0] == "connectorDeleteHandles"] and ("topics", "only-c1") not in nodes
        await open_world(w)


async def test_the_nodes_of_a_collection_with_no_index_on_the_connector_go_a_batch_at_a_time_and_a_batch_that_removes_nothing_fails(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    await open_world(w)
    monkeypatch.setattr(delete_module, "_CHUNK", 2)
    real = delete_module._remove_nodes
    sizes: list[int] = []

    async def spy(tx: Tx, org: str, coll: str, keys: list[str]) -> int:
        if coll == "stageStates":
            sizes.append(len(keys))
        return await real(tx, org, coll, keys)
    monkeypatch.setattr(delete_module, "_remove_nodes", spy)
    assert (await w.p.delete_connector_instance("c1", w.org))["success"] is True
    assert sizes == [2, 2, 1] and ("stageStates", "other-ss") in (await snapshot(w))[0]      # c1-ssv went with its record; another connector's stays

    await open_world(w)

    async def nothing(tx: Tx, org: str, coll: str, keys: list[str]) -> int:
        return 0 if coll == "stageStates" else await real(tx, org, coll, keys)
    monkeypatch.setattr(delete_module, "_remove_nodes", nothing)
    got = await w.p.delete_connector_instance("c1", w.org)
    assert got["success"] is False and got["partial"] is True and "stageStates" in got["error"]
    monkeypatch.setattr(delete_module, "_remove_nodes", real)
    assert (await w.p.delete_connector_instance("c1", w.org))["success"] is True
