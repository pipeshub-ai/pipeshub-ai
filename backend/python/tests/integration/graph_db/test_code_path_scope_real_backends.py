"""A code path is looked up within its connector and repo on the real graph stores: the neighbour tool answers exactly as the org-wide lookup did,
under every way a repo is named and with none (the ambiguity report), and reads the same few documents whether 10 or 200 repos hold the path.
Rows written before the scope existed are filled by the start-up backfill, which a crash leaves unmarked and a second run finishes.

The same checks as tests/integration/graph_db/postgres/test_code_path_scope.py. Environment: NEO4J_IT_URI, NEO4J_IT_PASSWORD.
"""
from __future__ import annotations

import contextlib
import logging
import os
import uuid
from typing import TYPE_CHECKING

import pytest

from app.agents.actions.code_graph import ops
from app.config.constants.arangodb import Connectors
from app.core.org_context import for_org
from app.migrations import code_path_scope_migration as migration
from app.models.entities import RecordGroup, RecordGroupType
from tests.integration.graph_db import code_path_scope_support as world_of
from tests.integration.real_graph import backend_unavailable, connect_neo4j

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

pytestmark = [pytest.mark.integration, pytest.mark.timeout(900)]
LOG = logging.getLogger("code-path-scope-real-backends")


async def _neo4j_strip_scope(g: IGraphDBProvider, org: str) -> None:
    await g.client.execute_query("MATCH (c:Codefiles) WHERE c.orgId = $org REMOVE c.connectorId, c.recordGroupId", {"org": org})
    await g.client.execute_query("MATCH (g:RecordGroup) WHERE g.orgId = $org REMOVE g.repoKey", {"org": org})


# Per store: how to connect, and how to strip the scope fields as rows written before them lack them.
STORES = {
    "neo4j": (connect_neo4j, _neo4j_strip_scope),
}


@pytest.fixture(params=list(STORES))
async def store(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[tuple[IGraphDBProvider, object]]:
    if os.environ.get("PYTEST_XDIST_WORKER"):
        pytest.skip("the backfill reads and marks every org of the shared store: run without pytest-xdist (CI runs these files serially)")
    connect, strip = STORES[request.param]
    try:
        g = await connect(LOG, monkeypatch)
    except Exception as exc:
        backend_unavailable(request.param, exc)
    try:
        yield g, strip
    finally:
        with contextlib.suppress(Exception):
            await g.disconnect()


def _org() -> str:
    return f"org-cps-{uuid.uuid4().hex[:10]}"


def ctx(org: str) -> contextlib.AbstractContextManager:
    return for_org(org, job="code-path-scope-test")


async def _one_call(g: IGraphDBProvider, org: str, connector: str, world: dict, counter: world_of.RowCounter) -> int:
    before = counter.rows
    with ctx(org):
        await world_of.get_neighbour_impl(graph_provider=g, connector_id=connector, org_id=org, user_id=world["user_id"], repo="acme/repo3",
                                          file_path=world_of.PATH, qualified_name="function:main", direction="any", depth=1)
    return counter.rows - before


async def test_the_scoped_lookup_answers_as_the_org_wide_one_and_reads_the_same_rows_at_10_and_200_repos(store, monkeypatch) -> None:
    g, _ = store
    counter = world_of.RowCounter(g)
    counter.install(monkeypatch)
    reads: dict[str, dict[int, int]] = {"org-wide": {}, "scoped": {}}
    for repos in (10, 200):
        org, connector = _org(), f"conn-cps-{uuid.uuid4().hex[:8]}"
        with ctx(org):
            world = await world_of.write_world(g, org, connector, repos)
        ops._SCOPED_ORGS.clear()
        with ctx(org):
            assert not await migration.scope_ready(g, org)
            before = await world_of.answers(g, org, connector, world)
        reads["org-wide"][repos] = await _one_call(g, org, connector, world, counter)
        await world_of.backfill(g, LOG)
        with ctx(org):
            assert await migration.scope_ready(g, org)
            after = await world_of.answers(g, org, connector, world)
        reads["scoped"][repos] = await _one_call(g, org, connector, world, counter)
        assert after == before
        assert before["acme/repo3"].get("neighbors") is not None and "error" not in before["acme/repo3"], before["acme/repo3"]
        assert before["None"].get("raised") == "AmbiguousRepoError", before["None"]       # 10 or 200 repos hold the path: the ambiguity is reported
    assert reads["scoped"][200] == reads["scoped"][10], reads
    assert reads["org-wide"][200] > reads["org-wide"][10], reads              # the old lookup read every repo's file at the path


async def test_rows_written_before_the_scope_are_filled_and_a_backfill_cut_short_leaves_no_marker(store, monkeypatch) -> None:
    g, strip = store
    org, connector = _org(), f"conn-cps-{uuid.uuid4().hex[:8]}"
    with ctx(org):
        world = await world_of.write_world(g, org, connector, 5)
    await strip(g, org)
    ops._SCOPED_ORGS.clear()
    with ctx(org):
        before = await world_of.answers(g, org, connector, world)

    real = g.backfill_code_path_scope
    calls = {"n": 0}

    async def cut_short(limit: int = 5000, *, org_id: str | None = None) -> int:
        calls["n"] += 1
        if calls["n"] > 1:
            raise RuntimeError("stopped mid-backfill")
        return await real(limit=3, org_id=org_id)
    monkeypatch.setattr(g, "backfill_code_path_scope", cut_short)
    await world_of.backfill(g, LOG)      # the org's failure is logged; it stays unmarked
    with ctx(org):
        assert not await migration.scope_ready(g, org)
        assert await world_of.answers(g, org, connector, world) == before      # half-filled rows: still the org-wide lookup
    monkeypatch.setattr(g, "backfill_code_path_scope", real)

    assert await world_of.backfill(g, LOG) > 0
    with ctx(org):
        assert await migration.scope_ready(g, org)
        files = {d["_key"]: d for d in await g.get_nodes_by_field_in("codeFiles", "_key", world["records"], raise_on_error=True)}
        groups = {d["_key"]: d for d in await g.get_nodes_by_field_in("recordGroups", "_key", [x for pair in world["groups"] for x in pair],
                                                                        raise_on_error=True)}
        assert {(d["connectorId"], d["recordGroupId"]) for d in files.values()} == {(connector, code) for _, code in world["groups"]}
        assert {groups[top]["repoKey"] for top, _ in world["groups"]} == {f"repo{i}" for i in range(5)}
        assert {groups[code]["repoKey"] for _, code in world["groups"]} == {"code repository"}
        assert await world_of.answers(g, org, connector, world) == before
    await g.backfill_code_path_scope()      # again: a filled row is not touched
    with ctx(org):
        again = {d["_key"]: d for d in await g.get_nodes_by_field_in("codeFiles", "_key", world["records"], raise_on_error=True)}
    assert {k: (d["connectorId"], d["recordGroupId"]) for k, d in again.items()} == {k: (d["connectorId"], d["recordGroupId"]) for k, d in files.items()}


async def test_a_repo_key_is_casefolded_in_python_not_by_the_store(store) -> None:
    """Cypher's toLower is not casefold: "Straße" must key as "strasse", as the tool computes it."""
    g, strip = store
    org = _org()
    with ctx(org):
        # The backfill walks the store's orgs, so the group's org exists, as it does for every group a connector writes.
        await g.batch_upsert_nodes([{"_key": org, "accountType": "enterprise", "isActive": True, "name": "path-scope"}], "organizations")
        group = RecordGroup(org_id=org, name="acme/Straße", external_group_id=f"ext-{org}", group_type=RecordGroupType.REPOSITORY,
                            connector_name=Connectors.GITHUB, connector_id="conn-cps")
        await g.batch_upsert_record_groups([group])
    await strip(g, org)
    await world_of.backfill(g, LOG)
    with ctx(org):
        rows = await g.get_nodes_by_filters("recordGroups", {"orgId": org, "repoKey": "strasse"}, return_fields=["_key"], raise_on_error=True)
    assert [r["_key"] for r in rows] == [group.id]


@pytest.mark.parametrize("how", ["batch_update_nodes", "update_node", "batch_upsert_nodes"])
async def test_a_code_record_moved_to_another_repo_is_found_in_the_new_repo_only(store, how) -> None:
    """The scoped lookup reads the code file's copy of its record's group: every write that moves the record moves the copy with it."""
    g, _ = store
    org, connector = _org(), f"conn-cps-{uuid.uuid4().hex[:8]}"
    with ctx(org):
        world = await world_of.write_world(g, org, connector, 5)
    await world_of.backfill(g, LOG)
    moved, new_group = world["records"][3], world["groups"][4][1]
    with ctx(org):
        if how == "batch_update_nodes":
            await g.batch_update_nodes([{"id": moved, "recordGroupId": new_group}], "records")
        elif how == "update_node":
            await g.update_node(moved, "records", {"recordGroupId": new_group})
        else:
            doc = await g.get_document(moved, "records")
            await g.batch_upsert_nodes([{**doc, "recordGroupId": new_group}], "records")
        scoped = {repo: await world_of.records_at(g, org, connector, world, repo) for repo in ("acme/repo3", "acme/repo4")}
    assert scoped == {"acme/repo3": [], "acme/repo4": sorted([moved, world["records"][4]])}, scoped
    ops._SCOPED_ORGS.clear()
    with ctx(org):
        await g.remove_sync_point(migration.marker_key(org), "syncPoints")         # the org-wide lookup, as the oracle
        assert not await migration.scope_ready(g, org)
        assert {repo: await world_of.records_at(g, org, connector, world, repo) for repo in ("acme/repo3", "acme/repo4")} == scoped


async def test_a_batch_that_updates_nothing_while_rows_remain_does_not_end_the_backfill(store, monkeypatch) -> None:
    g, strip = store
    await world_of.early_zero_backfill(g, LOG, monkeypatch, lambda org: strip(g, org))


async def test_an_external_org_is_filled_and_marked(store) -> None:
    g, strip = store
    await world_of.external_org_is_marked(g, LOG, lambda org: strip(g, org))


async def test_an_org_with_leftovers_does_not_keep_another_org_unmarked(store, monkeypatch) -> None:
    g, strip = store
    await world_of.org_with_leftovers_does_not_block_another(g, LOG, monkeypatch, lambda org: strip(g, org))
