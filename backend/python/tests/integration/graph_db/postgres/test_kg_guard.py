"""The completeness guard of the projection: every method of the provider that writes the graph is classified, and a new one that is not fails.

Static: the registry lists exactly the public methods that write graph.nodes or graph.edges, in their own source, in a private helper or in a module-level SQL text they use (an AST scan
of the provider package). Dynamic: an unclassified writer raises in strict mode, also inside an explicit transaction; a classified one does not; outside the provider it is logged, not
refused, unless strict. The database layer of the chokepoint (the triggers) is in test_kg_capture.py. With the projection off the connectors service leaves no row in the log.
"""
from __future__ import annotations

import ast
import re
import time
from pathlib import Path

import pytest

import app.services.graph_db.postgres as pgpkg
from app.config.constants.arangodb import DeleteSource
from app.services.graph_db.postgres.base import PgBase
from app.services.graph_db.postgres.kg import flush
from app.services.graph_db.postgres.kg.guard import instrument_writers
from app.services.graph_db.postgres.kg.registry import (
    NO_PROJECTION,
    PROJECTED,
    WRITERS,
    UnclassifiedProviderWrite,
)
from app.services.graph_db.postgres.kg.writers import KgWriter

from .kg_fresh import differences, graph_rows
from .pg_support import new_org
from .test_kg_derivation import ctx, held, prov
from .test_kg_projection_races import world2

pytestmark = [pytest.mark.integration, pytest.mark.timeout(120)]
WRITE = re.compile(r"\b(INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+graph\.(nodes|edges)\b", re.I)


def _functions(tree: ast.Module) -> list[ast.AST]:
    out: list[ast.AST] = []
    for n in tree.body:
        out.extend([n] if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef)) else
                   [m for m in n.body if isinstance(m, (ast.AsyncFunctionDef, ast.FunctionDef))] if isinstance(n, ast.ClassDef) else [])
    return out


def writers_in_source() -> dict[str, str]:
    """name -> file of every public function in the provider package that writes graph.nodes or graph.edges: in its own source, in a private helper or a module-level SQL text it
    uses. A call into another public method stops the walk there: that method is the one classified."""
    sources: dict[str, tuple[str, str]] = {}
    consts: set[str] = set()
    refs: dict[str, set[str]] = {}
    public: dict[str, str] = {}
    for f in sorted(Path(pgpkg.__file__).parent.glob("*.py")):
        src = f.read_text()
        tree = ast.parse(src)
        for node in tree.body:
            if isinstance(node, ast.Assign) and WRITE.search(ast.get_source_segment(src, node) or ""):
                consts.update(t.id for t in node.targets if isinstance(t, ast.Name))
        for fn in _functions(tree):
            seg = ast.get_source_segment(src, fn) or ""
            sources[fn.name] = (f.name, sources.get(fn.name, ("", ""))[1] + seg)
            refs.setdefault(fn.name, set()).update({x.id for x in ast.walk(fn) if isinstance(x, ast.Name)} | {x.attr for x in ast.walk(fn) if isinstance(x, ast.Attribute)})
            if not fn.name.startswith("_") and f.name != "provider.py":
                public[fn.name] = f.name

    def writes(name: str, seen: set[str]) -> bool:
        if name in seen or name not in refs:
            return False
        seen.add(name)
        if WRITE.search(sources.get(name, ("", ""))[1]) or refs[name] & consts:
            return True
        return any(writes(r, seen) for r in refs[name] if r.startswith("_"))

    return {name: f for name, f in public.items() if writes(name, set())}


def test_the_registry_lists_every_writer_of_the_provider() -> None:
    found = writers_in_source()
    assert sorted(set(found) - set(WRITERS)) == [], "a provider method writes the graph and is not classified in kg/registry.py"
    assert sorted(set(WRITERS) - set(found)) == [], "the registry names a method that no longer writes the graph"


def test_a_method_that_projects_nothing_says_why() -> None:
    for name, (kind, reason) in WRITERS.items():
        assert kind in (PROJECTED, NO_PROJECTION), name
        assert bool(reason) == (kind == NO_PROJECTION), name


class Rogue(PgBase):
    """A writer somebody added without classifying it, and one with the name of a classified method."""

    async def sneak_a_node_in(self, org: str, transaction: str | None = None) -> None:
        await self._q(lambda tx: tx.execute("/* pipeshub:test.sneak */ INSERT INTO graph.nodes (org_id, coll, id, doc) VALUES ($1, 'persons', 'p1', '{}'::jsonb) ON CONFLICT DO NOTHING", org),
                      transaction, write=True)

    async def update_node(self, org: str) -> None:           # the name of a classified method (core.CoreMixin.update_node)
        await self._q(lambda tx: tx.execute("/* pipeshub:test.update_node */ UPDATE graph.nodes SET doc = doc WHERE org_id = $1 AND coll = 'persons'", org), None, write=True)


class RogueInTxn(PgBase):
    """The same two writers, each able to run in a caller's explicit transaction."""

    async def sneak_a_node_in(self, org: str, transaction: str | None = None) -> None:
        await self._q(lambda tx: tx.execute("/* pipeshub:test.sneak */ INSERT INTO graph.nodes (org_id, coll, id, doc) VALUES ($1, 'persons', 'p1', '{}'::jsonb) ON CONFLICT DO NOTHING", org),
                      transaction, write=True)

    async def update_node(self, org: str, transaction: str | None = None) -> None:           # the name of a classified method
        await self._q(lambda tx: tx.execute("/* pipeshub:test.update_node */ UPDATE graph.nodes SET doc = doc WHERE org_id = $1 AND coll = 'persons'", org), transaction, write=True)


instrument_writers(Rogue)
instrument_writers(RogueInTxn)


async def test_an_unclassified_writer_raises_in_strict_mode(pg_db, monkeypatch) -> None:
    monkeypatch.setenv("PG_KG_GUARD", "strict")
    org = new_org()
    rogue = Rogue(None, pg_db)
    with ctx(org):
        with pytest.raises(UnclassifiedProviderWrite, match="sneak_a_node_in"):
            await rogue.sneak_a_node_in(org)
        await rogue.update_node(org)                                           # a classified name passes
        tid = await rogue.begin_transaction([], [])
        try:
            with pytest.raises(UnclassifiedProviderWrite, match="sneak_a_node_in"):
                await rogue.sneak_a_node_in(org, transaction=tid)                # attribution survives the transaction's own task
        finally:
            await rogue.rollback_transaction(tid)


async def test_outside_the_provider_a_graph_write_is_logged_and_refused_only_in_strict_mode(pg_db, monkeypatch, caplog) -> None:
    org = new_org()
    prov(pg_db)                                       # a provider installs the hook on its database

    async def raw(tx) -> None:
        await tx.execute("/* pipeshub:test.raw */ INSERT INTO graph.nodes (org_id, coll, id, doc) VALUES ($1, 'persons', 'p2', '{}'::jsonb) ON CONFLICT DO NOTHING", org)

    monkeypatch.setenv("PG_KG_GUARD", "")
    with ctx(org):
        await pg_db.run(raw, write=True)
    assert any("not classified" in r.message for r in caplog.records)
    monkeypatch.setenv("PG_KG_GUARD", "strict")
    with ctx(org), pytest.raises(UnclassifiedProviderWrite, match="outside the provider"):
        await pg_db.run(raw, write=True)


# The writers that reach graph.nodes and graph.edges through a private helper are classified PROJECTED, so what they change must reach kg: each runs on a small world, the
# flush projects what the triggers logged in the writer's own transaction, and kg then equals a whole projection of the graph.
async def _trash_then_restore(p, _org: str) -> None:
    await p.soft_delete_records(["x1"], "app2", delete_source="USER", batch_id="b2", deleted_by_user_id="u1")
    assert await p.restore_records([{"id": "x1"}], "b2") == ["x1"]


HELPERS = {
    "soft_delete_records": lambda p, _org: p.soft_delete_records(["x1"], "app2", delete_source="USER", batch_id="b1", deleted_by_user_id="u1"),
    "restore_records": _trash_then_restore,
    "delete_record": lambda p, org: p.delete_record("x1", "u1", org, delete_source=DeleteSource.CONNECTOR),
    "unlink_nodes_missed_by_full_sync": lambda p, _org: p.unlink_nodes_missed_by_full_sync("app1", {"records": ["fold"], "recordGroups": ["g1"]}),
    "link_record_to_group": lambda p, _org: p.link_record_to_group("f2", "g2", inherit=True),
    "update_node_if_match": lambda p, org: p.update_node_if_match("f1", "records", {"recordName": "Matched", "connectorId": "app1", "orgId": org, "recordType": "FILE", "origin": "CONNECTOR"}, "connectorId", "app1"),
    "remove_nodes_by_field": lambda p, _org: p.remove_nodes_by_field("records", "recordName", field_value="Two"),
    "delete_single_record": lambda p, _org: p.delete_single_record("x1"),
    "delete_records_and_relations": lambda p, _org: p.delete_records_and_relations("fold"),
    "delete_records_recursive": lambda p, _org: p.delete_records_recursive(["fold"], "app1"),
    "delete_parent_child_edge_to_record": lambda p, _org: p.delete_parent_child_edge_to_record("f1"),
    "delete_edges_touching_nodes": lambda p, _org: p.delete_edges_touching_nodes(["f1"], "records", "nodeRelations"),
    "delete_edges_by_source_keys": lambda p, _org: p.delete_edges_by_source_keys(["fold"], "nodeRelations"),
}


@pytest.mark.parametrize("name", sorted(HELPERS))
async def test_a_writer_classified_projected_through_a_helper_leaves_kg_equal_to_the_graph(pg_db, monkeypatch, name: str) -> None:
    monkeypatch.setenv("PG_KG_PROJECT", "1")
    monkeypatch.setenv("PG_KG_FLUSH", "inline")
    monkeypatch.setenv("PG_KG_STRICT", "1")
    assert WRITERS[name][0] == PROJECTED
    p, org = prov(pg_db), new_org()
    await world2(p, org)
    assert not await differences(p, org)
    before = [[tuple(map(str, r.values())) for r in rows] for rows in await graph_rows(p, org)]
    with ctx(org):
        await HELPERS[name](p, org)
    assert [[tuple(map(str, r.values())) for r in rows] for rows in await graph_rows(p, org)] != before, "the writer changed nothing: the case proves nothing"
    assert await log_rows(p, org) == 0
    assert not await differences(p, org)


async def quiet_world2(p, org: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """The shared world, written with no sweep of the org's change log running. With the projection off, the first write unit of the world spawns a sweep
    after its commit, and the units that follow take their own rows back while it holds the org's lock: a unit that finds the lock held leaves its rows, and the
    tests below count rows. The org counts as just swept, so the world is written without one; a test that wants a sweep clears ``_last_sweep`` afterwards."""
    monkeypatch.setattr(flush, "_last_sweep", {org: time.monotonic()})
    await world2(p, org)


async def log_rows(p, org: str) -> int:
    async def go(tx) -> int:
        return await tx.fetchval("SELECT count(*) FROM kg.graph_changes WHERE org_id = $1", org)
    with ctx(org):
        return await p._q(go, None)


async def other_roles_rows(p, org: str, monkeypatch: pytest.MonkeyPatch, n: int = 20) -> None:
    """Rows a unit of another service would leave (committed, by an earlier transaction): made by a unit the take-back does not recognise as the connectors service's own."""
    async def go(tx) -> None:
        await tx.execute("/* pipeshub:test.others */ INSERT INTO kg.graph_changes (org_id, ends) SELECT $1, ARRAY['3:f1'] FROM generate_series(1, $2)", org, n)
    with monkeypatch.context() as m:
        m.setattr(flush, "_own_role", lambda _tx: False)
        with ctx(org):
            await p._q(go, None, write=True)


async def test_with_the_projection_off_the_connectors_service_leaves_no_row_in_the_log(pg_db, monkeypatch) -> None:
    monkeypatch.delenv("PG_KG_PROJECT", raising=False)
    p, org = prov(pg_db), new_org()
    await quiet_world2(p, org, monkeypatch)
    monkeypatch.setattr(flush, "SWEEP_GAP_S", 0.0)
    monkeypatch.setattr(flush, "_last_sweep", {})
    assert await log_rows(p, org) == 0                                   # its own rows are taken back at commit
    await other_roles_rows(p, org, monkeypatch)
    assert await log_rows(p, org) == 20
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": "f1", "orgId": org, "recordName": "Again", "connectorId": "app1"}], "records")
    await flush.settle_sweeps()                                          # the sweep runs after the unit's commit, in a transaction of its own
    assert await log_rows(p, org) == 0


async def test_the_log_is_swept_by_any_write_unit_of_the_connectors_service_not_only_by_one_that_wrote_the_graph(pg_db, monkeypatch) -> None:
    """Rows of the indexing and query roles pile up when only they write the graph: the connectors service's next write of anything takes them."""
    monkeypatch.delenv("PG_KG_PROJECT", raising=False)
    p, org = prov(pg_db), new_org()
    await quiet_world2(p, org, monkeypatch)
    await other_roles_rows(p, org, monkeypatch, n=30)
    monkeypatch.setattr(flush, "SWEEP_GAP_S", 0.0)
    assert await log_rows(p, org) == 30

    async def writes_no_graph(tx) -> None:
        await tx.fetchval("/* pipeshub:test.no_graph */ SELECT 1")
    with ctx(org):
        await pg_db.run(writes_no_graph, write=True)
    await flush.settle_sweeps()
    assert await log_rows(p, org) == 0


async def broken_statement(self, *_a: object) -> int:
    """A take-back statement that fails the way a lock wait or a statement timeout does: the database aborts the transaction."""
    return await self.tx.fetchval("/* pipeshub:test.broken */ SELECT 1 / 0")


async def test_an_ack_that_fails_does_not_fail_the_business_write_and_the_sweep_takes_the_rows_later(pg_db, monkeypatch) -> None:
    monkeypatch.delenv("PG_KG_PROJECT", raising=False)
    monkeypatch.setattr(flush, "SWEEP_GAP_S", 1e9)                      # no sweep with this unit
    p, org = prov(pg_db), new_org()
    monkeypatch.setattr(KgWriter, "changes_ack", broken_statement)
    await world2(p, org)                                                # the writes commit although the ack of every unit failed
    with ctx(org):
        assert (await p.get_document("f1", "records")) is not None
    assert await log_rows(p, org) > 0                                   # the rows stay
    monkeypatch.undo()
    monkeypatch.delenv("PG_KG_PROJECT", raising=False)
    monkeypatch.setattr(flush, "SWEEP_GAP_S", 0.0)
    monkeypatch.setattr(flush, "_last_sweep", {})
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": "f1", "orgId": org, "recordName": "Later", "connectorId": "app1"}], "records")
    await flush.settle_sweeps()
    assert await log_rows(p, org) == 0


async def test_a_sweep_that_fails_does_not_fail_the_business_write(pg_db, monkeypatch, caplog) -> None:
    monkeypatch.delenv("PG_KG_PROJECT", raising=False)
    p, org = prov(pg_db), new_org()
    await quiet_world2(p, org, monkeypatch)
    await other_roles_rows(p, org, monkeypatch, n=20)
    monkeypatch.setattr(flush, "SWEEP_GAP_S", 0.0)
    monkeypatch.setattr(flush, "_last_sweep", {})
    monkeypatch.setattr(KgWriter, "changes_delete", broken_statement)
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": "swept", "orgId": org, "recordName": "S", "connectorId": "app1"}], "records")
    await flush.settle_sweeps()
    with ctx(org):
        assert (await p.get_document("swept", "records")) is not None   # the business write committed
    assert await log_rows(p, org) == 20                                 # the sweep failed and left the rows
    assert any("sweep of the change log failed" in r.message for r in caplog.records)


MERGE_FORMS = {
    "merge": "MERGE INTO graph.nodes n USING (SELECT $1::text AS org, 'merged1'::text AS id) s ON n.org_id = s.org AND n.coll = 'records' AND n.id = s.id WHEN NOT MATCHED THEN "
             "INSERT (org_id, coll, id, doc) VALUES (s.org, 'records', s.id, '{\"recordName\": \"M\", \"connectorId\": \"app1\", \"recordType\": \"FILE\", \"origin\": \"CONNECTOR\"}'::jsonb)",
    "quoted": "INSERT INTO \"graph\".\"nodes\" (org_id, coll, id, doc) VALUES ($1, 'records', 'merged1', "
              "'{\"recordName\": \"M\", \"connectorId\": \"app1\", \"recordType\": \"FILE\", \"origin\": \"CONNECTOR\"}'::jsonb)",
    "cte": "WITH w AS (INSERT INTO graph.nodes (org_id, coll, id, doc) VALUES ($1, 'records', 'merged1', "
           "'{\"recordName\": \"M\", \"connectorId\": \"app1\", \"recordType\": \"FILE\", \"origin\": \"CONNECTOR\"}'::jsonb) RETURNING 1) SELECT count(*) FROM w",
}


@pytest.mark.parametrize("form", sorted(MERGE_FORMS))
async def test_a_unit_that_wrote_the_graph_in_a_form_the_text_test_misses_is_still_flushed(pg_db, monkeypatch, form: str) -> None:
    """The capture trigger marks the transaction, so the commit hook does not depend on recognising the statement."""
    monkeypatch.setenv("PG_KG_PROJECT", "1")
    monkeypatch.setenv("PG_KG_FLUSH", "inline")
    p, org = prov(pg_db), new_org()
    await world2(p, org)
    sql = MERGE_FORMS[form]

    async def go(tx) -> None:
        await tx.execute("/* pipeshub:test.form */ " + sql, org)
    monkeypatch.setenv("PG_KG_GUARD", "")
    with ctx(org):
        await p._q(go, None, write=True)
    assert await log_rows(p, org) == 0                                  # projected and acknowledged in the writer's own transaction
    assert "merged1" in (await held(p, org))["records"]


@pytest.mark.parametrize("first", ["update_node", "sneak_a_node_in"])
async def test_each_call_of_an_explicit_transaction_is_judged_by_its_own_writer_not_the_first_calls(pg_db, monkeypatch, first: str) -> None:
    """The transaction's task keeps the context of the call that opened it; the writer of every later call has to be carried to it."""
    monkeypatch.setenv("PG_KG_GUARD", "strict")
    org = new_org()
    p = RogueInTxn(None, pg_db)
    with ctx(org):
        tid = await p.begin_transaction([], [])
        try:
            if first == "update_node":
                await p.update_node(org, transaction=tid)                                    # classified: the task starts under its name
                with pytest.raises(UnclassifiedProviderWrite, match="sneak_a_node_in"):
                    await p.sneak_a_node_in(org, transaction=tid)                            # an unclassified writer does not ride on it
            else:
                with pytest.raises(UnclassifiedProviderWrite, match="sneak_a_node_in"):
                    await p.sneak_a_node_in(org, transaction=tid)                            # unclassified: the task starts under its name
                await p.update_node(org, transaction=tid)                                    # a classified writer is not refused for it
        finally:
            await p.rollback_transaction(tid)
