"""A code path is looked up within its connector and repo on PostgreSQL: the neighbour tool answers exactly as the org-wide lookup did, under every
way a repo is named and with none (the ambiguity report), and reads the same few documents whether 10 or 200 repos hold the path. Rows written
before the scope existed are filled by the start-up backfill, which a crash leaves unmarked and a second run finishes.

Needs a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_ADMIN_URL, PG_POOLER=none.
"""
from __future__ import annotations

import asyncio
import logging
import os

import pytest

from app.agents.actions.code_graph import ops
from app.migrations import code_path_scope_migration as migration
from tests.integration.graph_db import code_path_scope_support as world_of

from .pg_support import new_org
from .test_kg_derivation import ctx
from .test_kg_read import _admin

# The backfill reads and marks every organisation of the database: these run alone.
pytestmark = [pytest.mark.integration, pytest.mark.timeout(900), pytest.mark.pg_table_lock]
LOG = logging.getLogger("code-path-scope")


async def _one_call(p, org: str, connector: str, world: dict, counter: world_of.RowCounter) -> int:
    before = counter.rows
    with ctx(org):
        await world_of.get_neighbour_impl(graph_provider=p, connector_id=connector, org_id=org, user_id=world["user_id"], repo="acme/repo3",
                                          file_path=world_of.PATH, qualified_name="function:main", direction="any", depth=1)
    return counter.rows - before


async def test_the_scoped_lookup_answers_as_the_org_wide_one_and_reads_the_same_rows_at_10_and_200_repos(pg_provider, monkeypatch) -> None:
    counter = world_of.RowCounter(pg_provider)
    counter.install(monkeypatch)
    reads: dict[str, dict[int, int]] = {"org-wide": {}, "scoped": {}}
    for repos in (10, 200):
        org, connector = new_org(), "conn-code"
        with ctx(org):
            world = await world_of.write_world(pg_provider, org, connector, repos)
        ops._SCOPED_ORGS.clear()
        with ctx(org):
            assert not await migration.scope_ready(pg_provider, org)
            before = await world_of.answers(pg_provider, org, connector, world)
        reads["org-wide"][repos] = await _one_call(pg_provider, org, connector, world, counter)
        await world_of.backfill(pg_provider, LOG)
        with ctx(org):
            assert await migration.scope_ready(pg_provider, org)
            after = await world_of.answers(pg_provider, org, connector, world)
        reads["scoped"][repos] = await _one_call(pg_provider, org, connector, world, counter)
        assert after == before
        assert before["acme/repo3"].get("neighbors") is not None and "error" not in before["acme/repo3"], before["acme/repo3"]
        assert before["None"].get("raised") == "AmbiguousRepoError", before["None"]       # 10 or 200 repos hold the path: the ambiguity is reported
    assert reads["scoped"][200] == reads["scoped"][10], reads
    assert reads["org-wide"][200] > reads["org-wide"][10], reads              # the old lookup read every repo's file at the path


async def test_rows_written_before_the_scope_are_filled_and_a_backfill_cut_short_leaves_no_marker(pg_provider, monkeypatch) -> None:
    org, connector = new_org(), "conn-code"
    with ctx(org):
        world = await world_of.write_world(pg_provider, org, connector, 5)
    await _admin("UPDATE graph.nodes SET doc = doc - 'connectorId' - 'recordGroupId' WHERE org_id = $1 AND coll = 'codeFiles'", org)
    await _admin("UPDATE graph.nodes SET doc = doc - 'repoKey' WHERE org_id = $1 AND coll = 'recordGroups'", org)
    ops._SCOPED_ORGS.clear()
    with ctx(org):
        before = await world_of.answers(pg_provider, org, connector, world)

    real = pg_provider.backfill_code_path_scope
    calls = {"n": 0}

    async def cut_short(limit: int = 5000, *, org_id: str | None = None) -> int:
        calls["n"] += 1
        if calls["n"] > 1:
            raise RuntimeError("stopped mid-backfill")
        return await real(limit=3, org_id=org_id)
    monkeypatch.setattr(pg_provider, "backfill_code_path_scope", cut_short)
    await world_of.backfill(pg_provider, LOG)      # the org's failure is logged; it stays unmarked
    with ctx(org):
        assert not await migration.scope_ready(pg_provider, org)
        assert await world_of.answers(pg_provider, org, connector, world) == before      # half-filled rows: still the org-wide lookup
    monkeypatch.setattr(pg_provider, "backfill_code_path_scope", real)

    assert await world_of.backfill(pg_provider, LOG) > 0
    with ctx(org):
        assert await migration.scope_ready(pg_provider, org)
        files = {d["_key"]: d for d in await pg_provider.get_nodes_by_field_in("codeFiles", "_key", world["records"], raise_on_error=True)}
        groups = {d["_key"]: d for d in await pg_provider.get_nodes_by_field_in("recordGroups", "_key", [g for pair in world["groups"] for g in pair],
                                                                                  raise_on_error=True)}
        assert {(d["connectorId"], d["recordGroupId"]) for d in files.values()} == {(connector, code) for _, code in world["groups"]}
        assert {groups[top]["repoKey"] for top, _ in world["groups"]} == {f"repo{i}" for i in range(5)}
        assert {groups[code]["repoKey"] for _, code in world["groups"]} == {"code repository"}
        assert await world_of.answers(pg_provider, org, connector, world) == before


async def test_a_repo_key_is_the_casefolded_last_segment() -> None:
    from app.models.entities import repo_key
    assert repo_key("Acme/Straße") == "strasse" and repo_key("solo") == "solo" and repo_key("a/") == "" and repo_key(None) is None


@pytest.mark.parametrize("how", ["batch_update_nodes", "update_node", "batch_upsert_nodes"])
async def test_a_code_record_moved_to_another_repo_is_found_in_the_new_repo_only(pg_provider, how) -> None:
    """The scoped lookup reads the code file's copy of its record's group: every write that moves the record moves the copy with it."""
    org, connector = new_org(), "conn-code"
    with ctx(org):
        world = await world_of.write_world(pg_provider, org, connector, 5)
    await world_of.backfill(pg_provider, LOG)
    moved, new_group = world["records"][3], world["groups"][4][1]
    with ctx(org):
        if how == "batch_update_nodes":
            await pg_provider.batch_update_nodes([{"id": moved, "recordGroupId": new_group}], "records")
        elif how == "update_node":
            await pg_provider.update_node(moved, "records", {"recordGroupId": new_group})
        else:
            doc = await pg_provider.get_document(moved, "records")
            await pg_provider.batch_upsert_nodes([{**doc, "recordGroupId": new_group}], "records")
        scoped = {repo: await world_of.records_at(pg_provider, org, connector, world, repo) for repo in ("acme/repo3", "acme/repo4")}
    assert scoped == {"acme/repo3": [], "acme/repo4": sorted([moved, world["records"][4]])}, scoped
    ops._SCOPED_ORGS.clear()
    await _admin("DELETE FROM graph.nodes WHERE org_id = $1 AND coll = 'syncPoints'", org)          # the org-wide lookup, as the oracle
    with ctx(org):
        assert {repo: await world_of.records_at(pg_provider, org, connector, world, repo) for repo in ("acme/repo3", "acme/repo4")} == scoped


async def test_a_write_of_other_records_or_without_the_scope_fields_does_no_code_file_work(pg_provider, monkeypatch) -> None:
    """The scope copy costs nothing where it cannot change anything: an upsert of other record types runs the writer's own statement alone,
    a patch that sets neither field runs exactly the original statement (code records too), and a code record written with the scope it had
    rewrites no code file."""
    from app.core.db import uow
    org, connector = new_org(), "conn-code"
    with ctx(org):
        world = await world_of.write_world(pg_provider, org, connector, 3)
    ran: list[str] = []
    real = uow.Tx._run

    async def run(self, op, sql, *args, internal=False) -> object:
        if "pipeshub:graph." in sql.split("*/", 1)[0]:     # the writer's statements, not the kg flush beside them
            ran.append(sql.split("*/", 1)[0])
        return await real(self, op, sql, *args, internal=internal)
    monkeypatch.setattr(uow.Tx, "_run", run)
    files = [{"_key": f"file-{i}", "orgId": org, "recordName": f"f{i}.txt", "recordType": "FILE", "connectorId": connector, "recordGroupId": world["groups"][0][1],
              "externalRecordId": f"ext-file-{i}", "origin": "CONNECTOR", "connectorName": "GITHUB", "version": 1} for i in range(20)]
    with ctx(org):
        await pg_provider.batch_upsert_nodes(files, "records")
        assert not [s for s in ran if "scope" in s], ran            # a record type the upsert knows: its own statement alone
        ran.clear()
        await pg_provider.batch_update_nodes([{"id": r, "updatedAtTimestamp": 1} for r in world["records"]], "records")
        await pg_provider.update_node(world["records"][0], "records", {"updatedAtTimestamp": 2})
        assert ran == ["/* pipeshub:graph.batch_update_nodes ", "/* pipeshub:graph.update_node "], ran
    before = await _code_file_versions(org)
    with ctx(org):
        doc = await pg_provider.get_document(world["records"][0], "records")
        await pg_provider.batch_upsert_nodes([doc], "records")                                     # a code record, its scope unchanged
        await pg_provider.batch_update_nodes([{"id": world["records"][0], "recordGroupId": doc["recordGroupId"]}], "records")
    assert await _code_file_versions(org) == before


async def _code_file_versions(org: str) -> dict[str, str]:
    """Each code file's row version (xmin): a rewrite changes it."""
    import asyncpg
    con = await asyncpg.connect(os.environ["PG_ADMIN_URL"])
    try:
        return {r["id"]: r["v"] for r in await con.fetch("SELECT id, xmin::text AS v FROM graph.nodes WHERE org_id = $1 AND coll = 'codeFiles'", org)}
    finally:
        await con.close()


async def test_two_writers_moving_one_code_record_leave_its_code_file_where_the_record_ends(pg_provider) -> None:
    """Writer 1 moves the record to B and holds its transaction; writer 2 moves it back to A and waits for the row. The code file ends with
    the record's final group, A: each writer copies the record as its own transaction left it."""
    org, connector = new_org(), "conn-code"
    with ctx(org):
        world = await world_of.write_world(pg_provider, org, connector, 2)
    rid, a, b = world["records"][0], world["groups"][0][1], world["groups"][1][1]
    with ctx(org):
        tid = await pg_provider.begin_transaction(read=[], write=["records", "codeFiles"])
        await pg_provider.update_node(rid, "records", {"recordGroupId": b}, transaction=tid)
        second = asyncio.ensure_future(_move(pg_provider, org, rid, a))
        await asyncio.sleep(0.5)
        assert not second.done()                                     # waiting for writer 1's row
        await pg_provider.commit_transaction(tid)
        await second
        record = await pg_provider.get_document(rid, "records")
        code_file = await pg_provider.get_document(rid, "codeFiles")
    assert record["recordGroupId"] == a and code_file["recordGroupId"] == a, (record["recordGroupId"], code_file["recordGroupId"])


async def _move(p, org: str, rid: str, group: str) -> None:
    with ctx(org):
        await p.batch_update_nodes([{"id": rid, "recordGroupId": group}], "records")


async def test_a_code_record_whose_group_is_cleared_clears_its_code_files_group(pg_provider) -> None:
    """An upsert that sets the group to null (the clear runs before the merge) takes the null to the code file."""
    org, connector = new_org(), "conn-code"
    with ctx(org):
        world = await world_of.write_world(pg_provider, org, connector, 2)
        rid = world["records"][0]
        doc = await pg_provider.get_document(rid, "records")
        await pg_provider.batch_upsert_nodes([{**doc, "recordGroupId": None}], "records")
        assert (await pg_provider.get_document(rid, "codeFiles")).get("recordGroupId") is None


async def test_a_batch_that_updates_nothing_while_rows_remain_does_not_end_the_backfill(pg_provider, monkeypatch) -> None:
    await world_of.early_zero_backfill(pg_provider, LOG, monkeypatch, _strip)


async def _strip(org: str) -> None:
    await _admin("UPDATE graph.nodes SET doc = doc - 'connectorId' - 'recordGroupId' WHERE org_id = $1 AND coll = 'codeFiles'", org)
    await _admin("UPDATE graph.nodes SET doc = doc - 'repoKey' WHERE org_id = $1 AND coll = 'recordGroups'", org)


async def test_an_external_org_is_filled_and_marked(pg_provider) -> None:
    await world_of.external_org_is_marked(pg_provider, LOG, _strip)


async def test_an_org_with_leftovers_does_not_keep_another_org_unmarked(pg_provider, monkeypatch) -> None:
    await world_of.org_with_leftovers_does_not_block_another(pg_provider, LOG, monkeypatch, _strip)
