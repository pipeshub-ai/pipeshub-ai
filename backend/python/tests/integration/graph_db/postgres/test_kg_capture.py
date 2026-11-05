"""The change capture (kg_0004): what a write to graph.nodes or graph.edges leaves in kg.graph_changes, as the writer's own role, in its transaction.

A write that changes what the projection reads logs the keys it changes (per connector for a node, the ends of an edge that its rows depend on); a write that changes nothing
the projection reads logs nothing. The mutants are the writes a trigger that looked at the wrong thing would miss: a delete, an update of the key columns, an update of an edge,
the connector of a node, an update of the edge key alone.
"""
from __future__ import annotations

import pytest

from .pg_support import new_org
from .test_kg_derivation import ctx, edge, prov

pytestmark = [pytest.mark.integration, pytest.mark.timeout(60)]


@pytest.fixture(autouse=True)
def log_is_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    """With the projection on, a unit leaves the rows it logged (with it off the connectors service takes its own back at commit)."""
    monkeypatch.setenv("PG_KG_PROJECT", "1")


async def log(p, org: str) -> list[tuple[str, list[str]]]:
    async def go(tx) -> list:
        return [(r["app_id"], sorted(r["ends"])) for r in await tx.fetch("SELECT app_id, ends FROM kg.graph_changes WHERE org_id = $1 ORDER BY id", org)]
    with ctx(org):
        return await p._q(go, None)


async def clear(p, org: str) -> None:
    async def go(tx) -> None:
        await tx.execute("/* pipeshub:test.clear_log */ SELECT kg.w_changes_delete(ARRAY(SELECT id FROM kg.graph_changes WHERE org_id = $1))", org)
    with ctx(org):
        await p._q(go, None, write=True)


async def raw(p, org: str, sql: str, *args: object) -> None:
    async def go(tx) -> None:
        await tx.execute(sql, *args)
    with ctx(org):
        await p._q(go, None, write=True)


REC = {"recordName": "R", "connectorId": "c1", "recordType": "FILE", "origin": "CONNECTOR"}


async def world(p, org: str) -> None:
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": "c1", "orgId": org, "name": "Drive", "type": "DRIVE"}, {"_key": "c2", "orgId": org, "name": "Other", "type": "DRIVE"}], "apps")
        await p.batch_upsert_nodes([{"_key": "r1", "orgId": org, **REC}, {"_key": "r2", "orgId": org, **REC}], "records")
        await p.batch_upsert_nodes([{"_key": "u1", "orgId": org, "email": "u@example.com"}], "users")
        await p.batch_create_edges([edge("r1", "records", "r2", "records", relationshipType="PARENT_CHILD")], "nodeRelations")
        await p.batch_create_edges([edge("u1", "users", "r1", "records", type="USER", role="READER")], "permission")
    await clear(p, org)


async def test_a_new_node_is_logged_under_its_connector_and_an_app_under_itself(pg_db) -> None:
    p, org = prov(pg_db), new_org()
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": "c1", "orgId": org, "name": "Drive", "type": "DRIVE"}], "apps")
        await p.batch_upsert_nodes([{"_key": "r1", "orgId": org, **REC}], "records")
        await p.batch_upsert_nodes([{"_key": "x1", "orgId": org, "recordName": "no connector"}], "records")
        await p.batch_upsert_nodes([{"_key": "d1", "orgId": org}], "departments")          # a collection kg does not hold
    assert await log(p, org) == [("c1", ["1:c1"]), ("c1", ["3:r1"]), ("", ["3:x1"])]


async def test_an_update_of_what_the_projection_does_not_read_logs_nothing_and_of_what_it_reads_logs_the_node(pg_db) -> None:
    p, org = prov(pg_db), new_org()
    await world(p, org)
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": "r1", "orgId": org, **REC, "indexingStatus": "COMPLETED", "generation": 4}], "records")
    assert await log(p, org) == []
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": "r1", "orgId": org, **{**REC, "recordName": "Renamed"}}], "records")
    assert await log(p, org) == [("c1", ["3:r1"])]


async def test_a_node_that_changes_connector_is_logged_under_both(pg_db) -> None:
    p, org = prov(pg_db), new_org()
    await world(p, org)
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": "r1", "orgId": org, **{**REC, "connectorId": "c2"}}], "records")
    assert sorted(await log(p, org)) == [("c1", ["3:r1"]), ("c2", ["3:r1"])]


async def test_a_deleted_node_is_logged(pg_db) -> None:
    p, org = prov(pg_db), new_org()
    await world(p, org)
    with ctx(org):
        await p.delete_nodes(["r2"], "records")
    assert ("c1", ["3:r2"]) in await log(p, org)


async def test_an_update_of_the_key_columns_logs_the_node_that_went_and_the_one_that_came(pg_db) -> None:
    p, org = prov(pg_db), new_org()
    await world(p, org)
    await raw(p, org, "/* pipeshub:test.rekey */ UPDATE graph.nodes SET id = 'r9' WHERE org_id = $1 AND coll = 'records' AND id = 'r2'", org)
    assert sorted(await log(p, org)) == [("c1", ["3:r2"]), ("c1", ["3:r9"])]


async def test_an_edge_is_logged_by_the_end_its_rows_depend_on(pg_db) -> None:
    p, org = prov(pg_db), new_org()
    await world(p, org)
    with ctx(org):
        await p.batch_create_edges([edge("r2", "records", "r1", "records", relationshipType="ATTACHMENT")], "nodeRelations")      # a hierarchy link changes the child
        await p.batch_create_edges([edge("r1", "records", "c1", "apps")], "belongsTo")                                            # a membership changes the source
        await p.batch_create_edges([edge("u1", "users", "r2", "records", type="USER", role="OWNER")], "permission")                # a grant changes the node it is on
        await p.batch_create_edges([edge("r1", "records", "r2", "records", relationshipType="REFERENCE")], "recordLinks")          # a collection kg does not read
    assert [e for _, e in await log(p, org)] == [["3:r1"], ["3:r1"], ["3:r2"]]


async def test_an_edge_update_is_logged_only_when_what_the_projection_reads_changed(pg_db) -> None:
    p, org = prov(pg_db), new_org()
    await world(p, org)
    await raw(p, org, "/* pipeshub:test.edge_note */ UPDATE graph.edges SET doc = doc || '{\"note\": \"x\"}' WHERE org_id = $1 AND coll = 'permission'", org)
    assert await log(p, org) == []
    await raw(p, org, "/* pipeshub:test.edge_role */ UPDATE graph.edges SET doc = jsonb_set(doc, '{role}', '\"OWNER\"') WHERE org_id = $1 AND coll = 'permission'", org)
    assert [e for _, e in await log(p, org)] == [["3:r1"]]


async def test_an_edge_retyped_to_or_from_a_hierarchy_relation_names_the_ends_of_both_versions(pg_db) -> None:
    p, org = prov(pg_db), new_org()
    await world(p, org)
    await raw(p, org, "/* pipeshub:test.retype */ UPDATE graph.edges SET doc = jsonb_set(doc, '{relationshipType}', '\"REFERENCE\"') WHERE org_id = $1 AND coll = 'nodeRelations'", org)
    assert [e for _, e in await log(p, org)] == [["3:r2"]]


async def test_a_deleted_edge_is_logged(pg_db) -> None:
    p, org = prov(pg_db), new_org()
    await world(p, org)
    with ctx(org):
        await p.batch_delete_edges([edge("r1", "records", "r2", "records", relationshipType="PARENT_CHILD")], "nodeRelations")
    assert [e for _, e in await log(p, org)] == [["3:r2"]]


async def test_an_update_of_the_edge_key_alone_logs_the_edge_that_went_and_the_one_that_came(pg_db) -> None:
    p, org = prov(pg_db), new_org()
    await world(p, org)
    await raw(p, org, "/* pipeshub:test.rekey_edge */ UPDATE graph.edges SET ekey = ekey || 'x' WHERE org_id = $1 AND coll = 'permission'", org)
    assert [e for _, e in await log(p, org)] == [["3:r1"], ["3:r1"]]


async def test_a_statement_that_writes_many_rows_logs_one_row_per_connector(pg_db) -> None:
    p, org = prov(pg_db), new_org()
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": f"r{i}", "orgId": org, **REC} for i in range(50)], "records")
    rows = await log(p, org)
    assert len(rows) == 1 and len(rows[0][1]) == 50


async def test_the_log_is_the_writers_own_organisations_and_a_service_cannot_log_for_another(pg_db) -> None:
    p, a, b = prov(pg_db), new_org(), new_org()
    with ctx(a):
        await p.batch_upsert_nodes([{"_key": "r1", "orgId": a, **REC}], "records")
    assert await log(p, b) == [] and len(await log(p, a)) == 1

    async def forge(tx) -> None:
        await tx.execute("/* pipeshub:test.forge */ INSERT INTO kg.graph_changes (org_id, ends) VALUES ($1, ARRAY['3:x'])", b)
    with ctx(a), pytest.raises(Exception, match="row-level security|violates"):
        await p._q(forge, None, write=True)


async def test_a_service_cannot_choose_the_identifier_or_the_transaction_id_of_a_row_it_logs(pg_db) -> None:
    """A claimed range of future identifiers would make the log insert of a legitimate write fail with a unique violation; the transaction id is the transaction's."""
    p, org = prov(pg_db), new_org()

    async def forge(tx) -> tuple:
        await tx.execute("/* pipeshub:test.forge_id */ INSERT INTO kg.graph_changes (org_id, id, txid, ends) OVERRIDING SYSTEM VALUE VALUES ($1, 999999999, '1'::xid8, ARRAY['3:x'])", org)
        row = await tx.fetchrow("SELECT id, txid = pg_current_xact_id() AS mine FROM kg.graph_changes WHERE org_id = $1", org)
        return row["id"], row["mine"]
    with ctx(org):
        got_id, mine = await p._q(forge, None, write=True)
    assert got_id != 999999999 and mine
    await world(p, org)                                   # legitimate writes go on logging, whatever was claimed

    async def claim_next(tx) -> None:
        for i in range(50):
            await tx.execute("/* pipeshub:test.claim */ INSERT INTO kg.graph_changes (org_id, id, ends) OVERRIDING SYSTEM VALUE VALUES ($1, $2, ARRAY['3:y'])", org, got_id + 1 + i)
    with ctx(org):
        await p._q(claim_next, None, write=True)
        await p.batch_upsert_nodes([{"_key": "after", "orgId": org, **REC}], "records")


async def test_an_over_long_node_id_is_logged_in_a_row_of_its_own(pg_db) -> None:
    """The drain sets aside a row it cannot read as a whole, so one key the vetted writers cannot take must not share a row with the keys that can be projected."""
    p, org = prov(pg_db), new_org()
    long_id = "L" * 300
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": k, "orgId": org, **REC} for k in ("a", long_id, "b")], "records")
        await p.batch_create_edges([edge("a", "records", long_id, "records", relationshipType="PARENT_CHILD"), edge("a", "records", "b", "records", relationshipType="PARENT_CHILD")],
                                   "nodeRelations")
    rows = await log(p, org)
    assert sorted(len(e) for _, e in rows) == [1, 1, 1, 2], rows
    assert [e for _, e in rows if any(len(k) > 258 for k in e)] == [["3:" + long_id], ["3:" + long_id]]       # the node's row and the edge's row
    assert sorted(tuple(e) for _, e in rows if not any(len(k) > 258 for k in e)) == [("3:a", "3:b"), ("3:b",)]


# ---- the capture decides what is a change by the same list of fields the projection reads --------------------------------------------------------------------------------------------
KG_SOURCES = ("model.py", "project.py", "dependents.py", "reader.py")


def fields_the_projection_reads() -> set[str]:
    """Every document field the kg modules name: a string given to ``.get`` and the field names given to ``listing_ts``."""
    import ast
    from pathlib import Path

    import app.services.graph_db.postgres.kg as pkg
    seen: set[str] = set()
    for name in KG_SOURCES:
        for node in ast.walk(ast.parse((Path(pkg.__file__).parent / name).read_text())):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get" and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                seen.add(node.args[0].value)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "listing_ts":
                seen.update(a.value for a in node.args[1:] if isinstance(a, ast.Constant) and isinstance(a.value, str))
    return seen


async def test_the_capture_lists_exactly_the_fields_the_projection_reads(pg_db) -> None:
    """A field the projection reads and the capture ignores is a change that is never projected; a field the capture keeps and nothing reads only costs a projection."""
    import json
    read = fields_the_projection_reads()
    probe = dict.fromkeys(sorted(read | {"indexingStatus", "generation", "zzzNobodyReads"}), "x")

    async def go(tx) -> tuple[set, set]:
        nodes = set()
        for coll in ("apps", "recordGroups", "records"):
            nodes |= set(json.loads(await tx.fetchval("SELECT kg.proj_doc($1, $2::jsonb)::text", coll, json.dumps(probe))))
        edges = set()
        for coll in ("permission", "nodeRelations", "authenticatedAs", "belongsTo"):
            edges |= set(json.loads(await tx.fetchval("SELECT kg.edge_sem($1, $2::jsonb)::text", coll, json.dumps(probe))))
        return nodes, edges
    p, org = prov(pg_db), new_org()
    with ctx(org):
        nodes, edges = await p._q(go, None)
    edge_fields = {"relationshipType", "role", "type", "connectorId"}          # the four fields reader.py selects of an edge document
    assert edges == edge_fields
    assert "zzzNobodyReads" not in nodes and "indexingStatus" not in nodes
    assert read <= nodes | edges, f"the projection reads {sorted(read - nodes - edges)} and the capture ignores it"
    assert nodes <= read, f"the capture keeps {sorted(nodes - read)} and the projection never reads it"
