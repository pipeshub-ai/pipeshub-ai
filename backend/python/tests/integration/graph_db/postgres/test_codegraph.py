"""The code graph, stage states and revision pointers on PostgreSQL: org scoping and the fail-closed rules, beside the cross-backend contract tests
of ``test_codegraph_contract.py``.

Needs a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_POOLER=none.
"""
from __future__ import annotations

import asyncio
import contextvars
import logging
import uuid

import pytest

from app.core import org_context
from app.core.org_context import (
    MissingOrgContext,
    OrgContext,
    SystemContextNotAllowed,
    bind,
)

from .pg_support import OrgBound, StandInProvider, new_org

pytestmark = pytest.mark.integration

REL = [{"from_id": "b1", "from_collection": "blocks", "to_id": "b2", "to_collection": "blocks", "relationshipType": "CALLS", "line": 3}]


def bound(pg_db: object, org: str | None = None) -> OrgBound:
    return OrgBound(StandInProvider(logging.getLogger("cg"), pg_db), org or new_org())


async def seed(p: OrgBound) -> None:
    await p.batch_upsert_nodes([{"_key": "b1", "recordId": "r1", "name": "A"}, {"_key": "b2", "recordId": "r1", "name": "B"}], "blocks")
    await p.batch_upsert_node_relations(REL)


async def test_links_blocks_and_stage_states_belong_to_their_org(pg_db) -> None:
    a, b = bound(pg_db), bound(pg_db)
    await seed(a)
    await seed(b)
    state = {"id": "s1", "virtualRecordId": "v1", "rev": "1", "stage": "entities", "status": "PENDING", "connectorId": "c", "updatedAtMs": 1}
    assert await a.stage_state_create(dict(state)) is True
    assert await b.stage_state_get("s1") is None
    assert await b.stage_state_compare_and_set("s1", "PENDING", "DONE", {}) is False          # another org's state is not this org's to settle
    assert (await a.stage_state_get("s1"))["status"] == "PENDING"
    assert [n["key"] for n in await a.get_neighbors_by_relationship_types("b1", "blocks", ["CALLS"], "outbound")] == ["b2"]
    assert await b.delete_blocks_for_records(["r1"]) == 2                                      # b's two blocks only
    assert await b.get_neighbors_by_relationship_types("b1", "blocks", ["CALLS"], "outbound") == []
    assert [n["key"] for n in await a.get_neighbors_by_relationship_types("b1", "blocks", ["CALLS"], "outbound")] == ["b2"]
    assert await a.get_nodes_by_field_prefix("blocks", "name", "A") != [] and await b.get_nodes_by_field_prefix("blocks", "name", "A") == []


async def test_a_field_projection_names_the_stored_id_and_the_collection(pg_db) -> None:
    p = bound(pg_db)
    await p.batch_upsert_nodes([{"_key": "k1", "name": "abc", "x": 1}], "things")
    got = await p.get_nodes_by_field_prefix("things", "name", "ab", return_fields=["_key", "_id", "name", "x", "missing"])
    assert got == [{"_key": "k1", "_id": "things/k1", "name": "abc", "x": 1, "missing": None}]
    assert await p.get_nodes_by_field_prefix("things", "name", "a%", return_fields=["_key"]) == []         # a prefix is text, not a pattern


async def test_a_null_in_a_code_file_reads_as_missing(pg_db) -> None:
    p = bound(pg_db)
    await p.batch_upsert_nodes([{"_key": "r1", "recordName": "main.py", "orgId": p.org}], "records")
    await p.batch_upsert_nodes([{"_key": "r1", "orgId": p.org}], "codeFiles")
    with bind(OrgContext(p.org, None, "job", "job:test", "t")):
        await pg_db.run(lambda tx: tx.execute("UPDATE graph.nodes SET doc = doc || '{\"filePath\": null, \"referencedNames\": null}'::jsonb WHERE org_id = $1 AND coll = 'codeFiles'", p.org), write=True)
    got = (await p.get_file_paths_for_records(p.org, ["r1"]))["r1"]
    assert (got.file_path, got.referenced_names, got.changed_names, got.has_code_file) == ("main.py", (), (), True)


CALLS = [
    ("get_nodes_by_field_prefix", ("blocks", "name", "A")), ("search_nodes_by_field_terms", ("blocks", "name", ["a"])), ("count_nodes_by_filters", ("blocks",)),
    ("has_nodes_by_filters", ("blocks",)), ("get_nodes_updated_since", ("blocks", "updatedAt", 0)), ("ensure_nodes", ([{"id": "x"}], "blocks")),
    ("update_sync_point_if", ("k", {"a": 1}, {"a": 2}, "syncPoints")), ("batch_upsert_node_relations", (REL,)),
    ("get_neighbors_by_relationship_types", ("b1", "blocks", ["CALLS"], "outbound")),
    ("get_neighbors_for_nodes_by_relationship_types", (["b1"], "blocks", ["CALLS"], "outbound")), ("get_edges_by_target_keys", (["blocks/b2"], "nodeRelations")),
    ("delete_edges_by_source_keys", (["blocks/b1"], "nodeRelations")), ("delete_edges_touching_nodes", (["b1"], "blocks", "nodeRelations")),
    ("get_edge_rollup_by_file_prefix", ("o", "src/", ["CALLS"], "outbound", "c")), ("get_file_paths_for_records", ("o", ["r1"])),
    ("delete_blocks_for_records", (["r1"],)), ("backfill_block_name_folded", ()), ("compare_and_set_record_fields", (["r1"], "1", {"a": 1})),
    ("stage_state_get", ("s",)), ("stage_states_for_revision", ("v", "1")), ("stage_state_create", ({"id": "s"},)),
    ("stage_state_compare_and_set", ("s", "A", "B", {})), ("get_stage_states_with_records", ("o", "c", "entities", "PENDING")),
    ("get_record_content_revs", (["r1"],)), ("stage_states_stale", (["PENDING"], 1, 10)), ("stage_states_delete_for_virtual_records", (["v"],)),
    ("get_switch_pointers", (None, 10)), ("get_revision_pointers", (None, 10)), ("stage_states_unheld_revisions", (["v"],)),
    ("stage_states_delete_unheld_revisions", ([("v", "1")],)),
]
SWEEPS = {"get_switch_pointers", "get_revision_pointers", "stage_states_stale", "stage_states_unheld_revisions", "stage_states_delete_unheld_revisions",
          "backfill_block_name_folded"}
KWARGS = {"get_stage_states_with_records": {"limit": 10}, "get_record_content_revs": {"status_field": "s", "statuses": ["A"]},
          "stage_states_unheld_revisions": {"updated_before_ms": 1}, "stage_states_delete_unheld_revisions": {"updated_before_ms": 1}}


def test_every_public_method_of_the_module_is_in_the_no_org_cases() -> None:
    from app.services.graph_db.postgres.codegraph import CodeGraphMixin
    public = {n for n, f in vars(CodeGraphMixin).items() if not n.startswith("__") and callable(f)}
    assert public - {"ensure_pipeline_schema", "_pointer_page", "_each_org"} == {c[0] for c in CALLS}


@pytest.mark.parametrize(("name", "args"), CALLS, ids=[c[0] for c in CALLS])
async def test_no_org_is_an_error_even_where_a_failed_read_answers_a_default(pg_provider, name: str, args: tuple) -> None:
    kwargs = KWARGS.get(name, {})
    with pytest.raises(MissingOrgContext):
        await getattr(pg_provider, name)(*args, **kwargs)
    if name in SWEEPS:      # these run across organisations under a system context (the tests below)
        return
    with org_context.system("test.no-org"), pytest.raises((SystemContextNotAllowed, MissingOrgContext)):
        await getattr(pg_provider, name)(*args, **kwargs)


async def test_a_stage_state_settles_once(pg_db) -> None:
    p = bound(pg_db)
    key = str(uuid.uuid4())
    await p.stage_state_create({"id": key, "virtualRecordId": "v", "rev": "1", "stage": "e", "status": "PENDING", "connectorId": "c", "updatedAtMs": 1, "attempt": 1})
    assert await p.stage_state_compare_and_set(key, "PENDING", "DONE", {}, expected_attempt=2) is False
    assert await p.stage_state_compare_and_set(key, "PENDING", "DONE", {"note": "x"}, expected_attempt=1) is True
    assert await p.stage_state_compare_and_set(key, "PENDING", "DONE", {}) is False
    assert (await p.stage_state_get(key))["status"] == "DONE"


def edge(f: str, t: str, rel: str, fc: str = "blocks", tc: str = "blocks", **kw: object) -> dict:
    return {"from_id": f, "from_collection": fc, "to_id": t, "to_collection": tc, "relationshipType": rel, **kw}


async def blocks(p: OrgBound, *ids: str) -> None:
    await p.batch_upsert_nodes([{"_key": i, "recordId": f"rec-{i}", "name": i.upper()} for i in ids], "blocks")


async def test_links_are_written_in_place_between_existing_ends_and_keyed_by_their_type(pg_db) -> None:
    p = bound(pg_db)
    await blocks(p, "b1", "b2")
    await p.batch_upsert_node_relations([edge("b1", "b2", "CALLS", line=1), edge("b1", "b2", "IMPORTS"), edge("b1", "ghost", "CALLS"), edge("b1", "b2", "CALLS", line=7)])
    got = {e["relationshipType"]: e for e in await p.get_edges_from_node("blocks/b1", "recordLinks")}
    assert set(got) == {"CALLS", "IMPORTS"} and got["CALLS"]["line"] == 7                                 # one per type; the last write wins; a missing end writes nothing
    first = got["CALLS"].get("createdAtTimestamp")
    await p.batch_upsert_node_relations([edge("b1", "b2", "CALLS", line=8, createdAtTimestamp=999)])
    again = {e["relationshipType"]: e for e in await p.get_edges_from_node("blocks/b1", "recordLinks")}
    assert again["CALLS"]["line"] == 8 and again["CALLS"].get("createdAtTimestamp") == first                 # a rebuild keeps the creation time


async def test_neighbours_by_type_direction_and_limit(pg_db) -> None:
    p = bound(pg_db)
    await blocks(p, "a", "b", "c")
    await p.batch_upsert_node_relations([edge("a", "b", "CALLS"), edge("c", "a", "CALLS"), edge("a", "c", "IMPORTS"), edge("a", "a", "CALLS")])
    out = await p.get_neighbors_by_relationship_types("a", "blocks", ["CALLS"], "outbound")
    assert sorted(n["key"] for n in out) == ["a", "b"] and all(n["relationshipType"] == "CALLS" for n in out)
    assert [n["key"] for n in await p.get_neighbors_by_relationship_types("a", "blocks", ["CALLS"], "inbound", limit=1)] in (["a"], ["c"])
    assert await p.get_neighbors_by_relationship_types("a", "blocks", [], "outbound") == []
    with pytest.raises(ValueError, match="direction"):
        await p.get_neighbors_by_relationship_types("a", "blocks", ["CALLS"], "sideways")
    both = await p.get_neighbors_for_nodes_by_relationship_types(["a"], "blocks", ["CALLS", "IMPORTS"], "any")
    assert sorted((n["direction"], n["key"], n["relationshipType"]) for n in both) == [("inbound", "c", "CALLS"), ("outbound", "a", "CALLS"), ("outbound", "b", "CALLS"), ("outbound", "c", "IMPORTS")]


async def test_edges_by_target_are_read_and_deleted_by_source_with_filters_and_exclusions(pg_db) -> None:
    p = bound(pg_db)
    await blocks(p, "s1", "s2", "t")
    await p.batch_upsert_node_relations([edge("s1", "t", "CALLS", build="old"), edge("s2", "t", "CALLS", build="new")])
    assert sorted(await p.get_edges_by_target_keys(["blocks/t"], "nodeRelations")) == ["blocks/s1", "blocks/s2"]
    assert await p.get_edges_by_target_keys(["blocks/t"], "nodeRelations", {"build": "new"}, return_field="_from") == ["blocks/s2"]
    assert await p.get_edges_by_target_keys(["blocks/s1"], "nodeRelations", return_field="_to") == []
    with pytest.raises(ValueError, match="return_field"):
        await p.get_edges_by_target_keys(["blocks/t"], "nodeRelations", return_field="x")
    assert await p.delete_edges_by_source_keys(["blocks/s1", "blocks/s2"], "nodeRelations", exclude={"build": "new"}) == 1       # the new build's edge is kept
    assert await p.get_edges_by_target_keys(["blocks/t"], "nodeRelations") == ["blocks/s2"]
    assert await p.delete_edges_touching_nodes(["t"], "blocks", "nodeRelations") == 1
    assert await p.delete_edges_touching_nodes([], "blocks", "nodeRelations") == 0


async def test_lookups_by_prefix_terms_counts_and_time(pg_db) -> None:
    p = bound(pg_db)
    await p.batch_upsert_nodes([{"_key": "f1", "filePath": "src/app/a.py", "updatedAt": 5, "kind": "x"}, {"_key": "f2", "filePath": "src/lib/b.py", "updatedAt": 9, "kind": "y"},
                                {"_key": "f3", "filePath": "docs/c.md", "updatedAt": 1, "kind": "x"}], "codeFiles")
    assert sorted(d["_key"] for d in await p.get_nodes_by_field_prefix("codeFiles", "filePath", "src/")) == ["f1", "f2"]
    assert [d["_key"] for d in await p.get_nodes_by_field_prefix("codeFiles", "filePath", "src/", {"kind": "y"})] == ["f2"]
    assert len(await p.get_nodes_by_field_prefix("codeFiles", "filePath", "", limit=2)) == 2
    assert sorted(d["_key"] for d in await p.search_nodes_by_field_terms("codeFiles", "filePath", ["APP/", "c.MD"])) == ["f1", "f3"]
    assert await p.search_nodes_by_field_terms("codeFiles", "filePath", []) == [] and await p.search_nodes_by_field_terms("codeFiles", "filePath", ["%"]) == []
    assert await p.count_nodes_by_filters("codeFiles") == 3 and await p.count_nodes_by_filters("codeFiles", {"kind": "x"}) == 2
    assert await p.count_nodes_by_filters("codeFiles", None, {"kind": ["x", "y"]}) == 3
    assert await p.has_nodes_by_filters("codeFiles", {"kind": "y"}) is True and await p.has_nodes_by_filters("codeFiles", {"kind": "z"}) is False
    assert sorted(d["_key"] for d in await p.get_nodes_updated_since("codeFiles", "updatedAt", 4)) == ["f1", "f2"]
    assert await p.get_nodes_updated_since("codeFiles", "updatedAt", 4, {"kind": "y"}, ["_key", "filePath"]) == [{"_key": "f2", "filePath": "src/lib/b.py"}]


async def test_the_edge_rollup_by_file_prefix_counts_links_between_files_of_one_connector(pg_db) -> None:
    p = bound(pg_db)
    org = p.org
    await p.batch_upsert_nodes([{"_key": "ra", "orgId": org, "connectorId": "c1"}, {"_key": "rb", "orgId": org, "connectorId": "c1"}, {"_key": "rz", "orgId": org, "connectorId": "c2"}], "records")
    await p.batch_upsert_nodes([{"_key": "ra", "orgId": org, "filePath": "src/a.py"}, {"_key": "rb", "orgId": org, "filePath": "lib/b.py"}, {"_key": "rz", "orgId": org, "filePath": "src/z.py"}], "codeFiles")
    await p.batch_upsert_nodes([{"_key": "ba1", "recordId": "ra"}, {"_key": "ba2", "recordId": "ra"}, {"_key": "bb", "recordId": "rb"}, {"_key": "bz", "recordId": "rz"}], "blocks")
    await p.batch_upsert_node_relations([edge("ba1", "bb", "CALLS"), edge("ba2", "bb", "CALLS"), edge("bz", "bb", "CALLS")])
    rows = await p.get_edge_rollup_by_file_prefix(org, "src/", ["CALLS"], "outbound", "c1")
    assert [(r["srcRecord"], r["dstRecord"], r["rel"], r["dir"], r["n"]) for r in rows] == [("ra", "rb", "CALLS", "outbound", 2)]          # not another connector's file, not another prefix
    assert await p.get_edge_rollup_by_file_prefix(_other(org), "src/", ["CALLS"], "outbound", "c1") == []
    with pytest.raises(ValueError, match="direction"):
        await p.get_edge_rollup_by_file_prefix(org, "src/", ["CALLS"], "up", "c1")


def _other(org: str) -> str:
    return new_org() if org else org


async def test_blocks_go_with_their_edges_and_a_name_is_folded_on_demand(pg_db) -> None:
    p = bound(pg_db)
    await p.batch_upsert_nodes([{"_key": "b1", "recordId": "r1", "name": "MixedCase"}, {"_key": "b2", "recordId": "r2", "name": "Other", "nameFolded": "other"}], "blocks")
    await p.batch_upsert_node_relations([edge("b1", "b2", "CALLS")])
    assert await p.backfill_block_name_folded() == 1 and await p.backfill_block_name_folded() == 0
    assert (await p.get_document("b1", "blocks"))["nameFolded"] == "mixedcase"
    assert await p.delete_blocks_for_records(["r1"]) == 1 and await p.delete_blocks_for_records([]) == 0
    assert await p.get_edges_to_node("blocks/b2", "recordLinks") == []


async def test_record_fields_are_written_only_while_the_record_holds_the_revision_and_content_revisions_are_read(pg_db) -> None:
    p = bound(pg_db)
    await p.batch_upsert_nodes([{"_key": "r1", "contentRev": "1", "indexingStatus": "QUEUED"}, {"_key": "r2", "contentRev": "2", "indexingStatus": "QUEUED"},
                                {"_key": "r3", "contentRev": "1", "indexingStatus": "FAILED", "isDeleted": True}], "records")
    assert sorted(await p.compare_and_set_record_fields(["r1", "r2", "r3", "r1", ""], "1", {"indexingStatus": "COMPLETED", "extractionStatus": "DONE"})) == ["r1", "r3"]
    assert (await p.get_document("r1", "records"))["extractionStatus"] == "DONE" and (await p.get_document("r2", "records")).get("extractionStatus") is None
    assert await p.compare_and_set_record_fields([], "1", {}) == []
    got = await p.get_record_content_revs(["r1", "r2", "r3"], status_field="indexingStatus", statuses=["COMPLETED", "QUEUED"])
    assert got == {"r1": "1", "r2": "2"}                                                                      # a deleted record is not selected
    assert await p.get_record_content_revs(["r1", "r2"], status_field="indexingStatus", statuses=["COMPLETED", "QUEUED"], exclude_indexing_statuses=["COMPLETED"]) == {"r2": "2"}
    assert await p.get_record_content_revs(["r1"], status_field="indexingStatus", statuses=[]) == {}


async def test_stage_states_by_revision_connector_age_and_what_no_record_holds(pg_db) -> None:
    p = bound(pg_db)
    await p.batch_upsert_nodes([{"_key": "r1", "virtualRecordId": "v1", "contentRev": "2", "connectorId": "c", "indexingStatus": "QUEUED"},
                                {"_key": "ph", "virtualRecordId": "v1", "contentRev": "2", "connectorId": "c", "isPlaceholder": True}], "records")

    def st(i: str, rev: str, status: str = "PENDING", ms: int = 10) -> dict:
        return {"id": i, "virtualRecordId": "v1", "rev": rev, "stage": "entities", "status": status, "connectorId": "c", "updatedAtMs": ms}
    for d in (st("s1", "1", ms=5), st("s2", "2", ms=6), st("s3", "3", "DONE", ms=7)):
        assert await p.stage_state_create(d)
    assert [s["id"] for s in await p.stage_states_for_revision("v1", "2")] == ["s2"]
    assert [s["id"] for s in await p.stage_states_stale(["PENDING"], 100, 10)] == ["s1", "s2"]
    assert [s["id"] for s in await p.stage_states_stale(["PENDING"], 6, 10)] == ["s1"]
    page = await p.get_stage_states_with_records(p.org, "c", "entities", "PENDING", limit=10)
    assert page == [("s1", []), ("s2", ["r1"])]                                                              # a placeholder holds nothing; a state none holds is listed empty
    assert await p.get_stage_states_with_records(p.org, "c", "entities", "PENDING", limit=1, after_key="s1") == [("s2", ["r1"])]
    assert await p.get_stage_states_with_records(p.org, "c", "entities", "PENDING", limit=10, exclude_indexing_statuses=["QUEUED"]) == [("s1", []), ("s2", [])]
    assert await p.get_stage_states_with_records(_other(p.org), "c", "entities", "PENDING", limit=10) == []
    assert await p.stage_states_unheld_revisions(["v1"], updated_before_ms=100) == [("v1", "1"), ("v1", "3")]
    assert await p.stage_states_unheld_revisions(["v1"], updated_before_ms=6) == [("v1", "1")]
    assert await p.stage_states_delete_unheld_revisions([("v1", "1"), ("v1", "2")], updated_before_ms=100) == 1       # revision 2 is held again by the record
    assert await p.stage_states_delete_for_virtual_records(["v1"]) == 2 and await p.stage_states_delete_for_virtual_records([]) == 0


async def test_pointers_are_paged_by_key(pg_db) -> None:
    p = bound(pg_db)
    await p.batch_upsert_nodes([{"_key": f"v{i}", "documentId": f"d{i}", "switchSeq": i, "retired": ["x"] if i else "bad", "currentRev": "2", "previousRev": "1"} for i in range(3)],
                               "virtualRecordToDocIdMapping")
    sw = await p.get_switch_pointers(None, 2)
    assert [(x["virtualRecordId"], x["orgId"], x["documentId"]) for x in sw] == [("v0", p.org, "d0"), ("v1", p.org, "d1")]
    assert sw[0]["retired"] == [] and sw[1]["retired"] == ["x"]                                                # a stored value that is not a list reads as none
    assert [x["virtualRecordId"] for x in await p.get_revision_pointers("v1", 5)] == ["v2"] and await p.get_revision_pointers(None, 0) == []


async def test_two_foreign_keys_of_one_type_between_a_pair_are_two_links(pg_db) -> None:
    """As Neo4j's MERGE, which keys a link by its type and its constraint name."""
    p = bound(pg_db)
    await p.batch_upsert_nodes([{"_key": "t1"}, {"_key": "t2"}], "records")
    await p.batch_upsert_node_relations([edge("t1", "t2", "FOREIGN_KEY", "records", "records", constraintName="fk_a"),
                                         edge("t1", "t2", "FOREIGN_KEY", "records", "records", constraintName="fk_b"),
                                         edge("t1", "t2", "FOREIGN_KEY", "records", "records", constraintName="fk_a", note="again")])
    got = await p.get_neighbors_by_relationship_types("t1", "records", ["FOREIGN_KEY"], "outbound")
    assert len(got) == 2 and {n["relationshipType"] for n in got} == {"FOREIGN_KEY"}
    edges = await p.get_edges_from_node("records/t1", "recordLinks")
    assert sorted(e["constraintName"] for e in edges) == ["fk_a", "fk_b"] and next(e for e in edges if e["constraintName"] == "fk_a")["note"] == "again"


def ctx(org: str) -> OrgContext:
    return OrgContext(org, None, "job", "job:test", "t")


async def test_a_stage_state_claimed_while_the_sweep_waits_is_not_deleted(pg_db) -> None:
    """The delete locks the states first and decides after: a state another writer claims (a newer ``updatedAtMs``) while it waits stays."""
    p = bound(pg_db)
    await p.stage_state_create({"id": "s1", "virtualRecordId": "v1", "rev": "1", "stage": "e", "status": "PENDING", "connectorId": "c", "updatedAtMs": 5})
    assert await p.stage_states_unheld_revisions(["v1"], updated_before_ms=1000) == [("v1", "1")]
    apart = contextvars.copy_context()                                  # the other writer's own context, not inside the open unit of work
    with bind(ctx(p.org)):
        async with pg_db.uow(write=True) as tx:
            await tx.execute("UPDATE graph.nodes SET doc = doc || '{\"updatedAtMs\": 100000}'::jsonb WHERE org_id = $1 AND coll = 'stageStates' AND id = 's1'", p.org)
            sweep = asyncio.get_running_loop().create_task(p.stage_states_delete_unheld_revisions([("v1", "1")], updated_before_ms=1000), context=apart)
            await asyncio.sleep(0.5)
            assert not sweep.done()                                        # waiting for the row, not deciding on its snapshot
    assert await sweep == 0
    assert await p.stage_state_get("s1") is not None


async def test_the_revision_guard_holds_between_two_writers(pg_db) -> None:
    """A write guarded by the revision waits for a writer that is moving the record on and then writes nothing."""
    p = bound(pg_db)
    await p.batch_upsert_nodes([{"_key": "r1", "contentRev": "1", "indexingStatus": "QUEUED"}], "records")
    apart = contextvars.copy_context()
    with bind(ctx(p.org)):
        async with pg_db.uow(write=True) as tx:
            await tx.execute("UPDATE graph.nodes SET doc = doc || '{\"contentRev\": \"2\"}'::jsonb WHERE org_id = $1 AND coll = 'records' AND id = 'r1'", p.org)
            stale = asyncio.get_running_loop().create_task(p.compare_and_set_record_fields(["r1"], "1", {"indexingStatus": "COMPLETED"}), context=apart)
            await asyncio.sleep(0.5)
            assert not stale.done()
    assert await stale == []
    assert (await p.get_document("r1", "records"))["indexingStatus"] == "QUEUED"
    wins = await asyncio.gather(*(p.compare_and_set_record_fields(["r1"], "2", {"who": w}) for w in "ab"))
    assert sorted(len(w) for w in wins) == [1, 1] and (await p.get_document("r1", "records"))["who"] in "ab"       # each holds the revision: both apply, last wins


async def test_the_sweeps_run_for_every_org_under_a_system_context(pg_db) -> None:
    orgs = [new_org(), new_org()]
    ps = [bound(pg_db, o) for o in orgs]
    tag = uuid.uuid4().hex
    for i, p in enumerate(ps):
        await p.batch_upsert_nodes([{"_key": p.org, "name": f"org{i}"}], "organizations")
        await p.batch_upsert_nodes([{"_key": f"b{i}", "recordId": "r", "name": "MixedCase"}], "blocks")
        await p.batch_upsert_nodes([{"_key": f"v{i}-{tag}", "documentId": "d", "switchSeq": 1, "retired": "[\"x\"]", "currentRev": "2"}], "virtualRecordToDocIdMapping")
        await p.stage_state_create({"id": f"s{i}-{tag}", "virtualRecordId": f"v{i}-{tag}", "rev": "1", "stage": "e", "status": "PENDING", "connectorId": "c", "updatedAtMs": 5})
    sys_p = StandInProvider(logging.getLogger("cg"), pg_db)
    mine = {f"v{i}-{tag}" for i in range(2)}
    vids = sorted(mine)
    with org_context.system("test.sweeps"):
        sw = [x for x in await sys_p.get_switch_pointers(None, 100000) if x["virtualRecordId"] in mine]
        assert {x["orgId"] for x in sw} == set(orgs) and all(x["retired"] == ["x"] for x in sw)         # a JSON string of a list is decoded as on Neo4j
        assert {x["orgId"] for x in await sys_p.get_revision_pointers(None, 100000) if x["virtualRecordId"] in mine} == set(orgs)
        assert {d["id"] for d in await sys_p.stage_states_stale(["PENDING"], 1000, 100000)} >= {f"s{i}-{tag}" for i in range(2)}
        assert await sys_p.stage_states_unheld_revisions(vids, updated_before_ms=1000) == [(v, "1") for v in vids]
        assert await sys_p.backfill_block_name_folded(100000) >= 2
        assert await sys_p.stage_states_delete_unheld_revisions([(v, "1") for v in vids], updated_before_ms=1000) == 2
    assert [(await p.get_document("b" + str(i), "blocks"))["nameFolded"] for i, p in enumerate(ps)] == ["mixedcase"] * 2
    assert [await p.stage_state_get(f"s{i}-{tag}") for i, p in enumerate(ps)] == [None, None]


async def test_the_system_sweeps_cap_one_pass_by_the_limit(pg_db) -> None:
    orgs = [new_org(), new_org()]
    for i, o in enumerate(orgs):
        p = bound(pg_db, o)
        await p.batch_upsert_nodes([{"_key": o, "name": "o"}], "organizations")
        await p.batch_upsert_nodes([{"_key": f"c{i}-{j}", "recordId": "r", "name": "N"} for j in range(3)], "blocks")
    with org_context.system("test.sweeps"):
        assert await StandInProvider(logging.getLogger("cg"), pg_db).backfill_block_name_folded(2) == 2


async def test_a_sync_point_is_updated_only_while_it_holds_the_expected_values_and_a_null_clears(pg_db) -> None:
    p = bound(pg_db)
    await p.upsert_sync_point("sp", {"cursor": "c1", "n": 1}, "syncPoints")
    assert await p.update_sync_point_if("sp", {"cursor": "other"}, {"cursor": "c2"}, "syncPoints") is False
    assert await p.update_sync_point_if("nope", {}, {"cursor": "c2"}, "syncPoints") is False
    assert await p.update_sync_point_if("sp", {"cursor": "c1"}, {"cursor": "c2", "n": None}, "syncPoints") is True
    got = await p.get_sync_point("sp", "syncPoints")
    assert got["cursor"] == "c2" and "n" not in got                                                                    # cleared, not stored as JSON null
    assert await p.update_sync_point_if("sp", {"missing": None}, {"cursor": "c3"}, "syncPoints") is True            # an absent property equals null


async def test_neighbours_by_direction_with_a_self_loop(pg_db) -> None:
    p = bound(pg_db)
    await blocks(p, "a", "b", "c")
    await p.batch_upsert_node_relations([edge("a", "b", "CALLS"), edge("c", "a", "CALLS"), edge("a", "a", "CALLS"), edge("a", "c", "IMPORTS")])

    async def keys(direction: str, types: list[str]) -> list[str]:
        return sorted(n["key"] for n in await p.get_neighbors_by_relationship_types("a", "blocks", types, direction))
    assert await keys("outbound", ["CALLS"]) == ["a", "b"]
    assert await keys("inbound", ["CALLS"]) == ["a", "c"]
    assert await keys("outbound", ["IMPORTS"]) == ["c"] and await keys("inbound", ["IMPORTS"]) == []
    many = await p.get_neighbors_for_nodes_by_relationship_types(["a", "c"], "blocks", ["CALLS"], "any")
    assert sorted((n["anchorKey"], n["key"]) for n in many) == [("a", "a"), ("a", "b"), ("a", "c"), ("c", "a")]


async def test_one_batch_of_links_merges_repeats_clears_nulls_and_keeps_types_apart(pg_db) -> None:
    p = bound(pg_db)
    await p.batch_upsert_nodes([{"_key": "t1"}, {"_key": "t2"}], "records")
    named = edge("t1", "t2", "CALLS", "records", "records", line=3)
    unnamed = {"from_id": "t1", "to_id": "t2", "relationshipType": "CALLS", "confidence": 0.5}
    await p.batch_upsert_node_relations([named, unnamed, edge("t1", "t2", "IMPORTS", "records", "records")])        # one edge named two ways, and a second type
    got = {e["relationshipType"]: e for e in await p.get_edges_from_node("records/t1", "recordLinks")}
    assert sorted(got) == ["CALLS", "IMPORTS"] and got["CALLS"]["line"] == 3 and got["CALLS"]["confidence"] == 0.5
    await p.batch_upsert_node_relations([edge("t1", "t2", "CALLS", "records", "records", line=None)])
    again = {e["relationshipType"]: e for e in await p.get_edges_from_node("records/t1", "recordLinks")}
    assert "line" not in again["CALLS"] and again["CALLS"]["confidence"] == 0.5


async def test_an_end_without_a_collection_is_the_record_before_a_block(pg_db) -> None:
    p = bound(pg_db)
    await p.batch_upsert_nodes([{"_key": "same"}, {"_key": "other"}], "records")
    await p.batch_upsert_nodes([{"_key": "same", "recordId": "x"}], "blocks")
    await p.batch_upsert_node_relations([{"from_id": "same", "to_id": "other", "relationshipType": "CALLS"}])
    assert [e["from_collection"] for e in await p.get_edges_to_node("records/other", "recordLinks")] == ["records"]


async def test_hierarchy_types_between_one_pair_are_one_edge_as_in_the_core(pg_db) -> None:
    """``PARENT_CHILD`` and ``ATTACHMENT`` are the hierarchy, one edge per pair (the access walk reads it); link types are separate edges. Never links."""
    p = bound(pg_db)
    await p.batch_upsert_nodes([{"_key": "t1"}, {"_key": "t2"}], "records")
    await p.batch_upsert_node_relations([edge("t1", "t2", "PARENT_CHILD", "records", "records"), edge("t1", "t2", "ATTACHMENT", "records", "records")])
    assert len(await p.get_edges_from_node("records/t1", "nodeRelations")) == 1 and await p.get_edges_from_node("records/t1", "recordLinks") == []
    assert await p.delete_edges_by_source_keys(["records/t1"], "recordLinks") == 0
    assert await p.get_edges_by_target_keys(["records/t2"], "recordLinks") == []


async def test_a_code_file_prefix_is_a_range_that_takes_percent_and_underscore_literally(pg_db) -> None:
    p = bound(pg_db)
    await p.batch_upsert_nodes([{"_key": k, "filePath": f} for k, f in (("c1", "a_c/x.py"), ("c2", "abc/x.py"), ("c3", "a%c/y.py"), ("c4", "b/z.py"))], "codeFiles")
    assert sorted(d["id"] for d in await p.get_nodes_by_field_prefix("codeFiles", "filePath", "a_")) == ["c1"]
    assert sorted(d["id"] for d in await p.get_nodes_by_field_prefix("codeFiles", "filePath", "a%")) == ["c3"]
    assert len(await p.get_nodes_by_field_prefix("codeFiles", "filePath", "")) == 4
