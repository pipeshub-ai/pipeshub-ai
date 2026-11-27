"""A code path is looked up within its connector and repo on PostgreSQL: the neighbour tool answers exactly as the org-wide lookup did, under every
way a repo is named and with none (the ambiguity report), and reads the same few documents whether 10 or 200 repos hold the path. Rows written
before the scope existed are filled by the start-up backfill, which a crash leaves unmarked and a second run finishes.

Needs a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_ADMIN_URL, PG_POOLER=none.
"""
from __future__ import annotations

import logging

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

    async def cut_short(limit: int = 5000) -> int:
        calls["n"] += 1
        if calls["n"] > 1:
            raise RuntimeError("stopped mid-backfill")
        return await real(limit=3)
    monkeypatch.setattr(pg_provider, "backfill_code_path_scope", cut_short)
    with pytest.raises(RuntimeError):
        await world_of.backfill(pg_provider, LOG)
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
