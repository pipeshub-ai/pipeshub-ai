"""A code path is looked up within its connector and repo on ArangoDB: the neighbour tool answers exactly as the org-wide lookup did, under every
way a repo is named and with none (the ambiguity report), and reads the same few documents whether 10 or 200 repos hold the path. Rows written
before the scope existed are filled by the start-up backfill, which a crash leaves unmarked and a second run finishes.

Needs ARANGO_IT_URL (see tests/integration/real_graph.py); without it the tests skip.
"""
from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

import pytest

from app.agents.actions.code_graph import ops
from app.config.constants.arangodb import CollectionNames
from app.core.org_context import for_org
from app.migrations import code_path_scope_migration as migration
from tests.integration.graph_db import code_path_scope_support as world_of
from tests.integration.real_graph import backend_unavailable, connect_arango

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

pytestmark = [pytest.mark.integration, pytest.mark.timeout(900)]
LOG = logging.getLogger("code-path-scope-arango")
ARANGO_DB = "code_path_scope_it"


@pytest.fixture
async def arango() -> AsyncIterator[IGraphDBProvider]:
    try:
        graph = await connect_arango(LOG, ARANGO_DB)
    except Exception as exc:
        backend_unavailable("arango", exc)
    yield graph
    await graph.disconnect()


def _new_org() -> tuple[str, str]:
    run = uuid.uuid4().hex[:12]
    return f"org-ps-{run}", f"conn-ps-{run}"


async def _strip_scope(g: IGraphDBProvider, org: str) -> None:
    """Make the org's rows as they were written before the scope existed."""
    await g.http_client.execute_aql(
        "FOR c IN @@c FILTER c.orgId == @org REPLACE c WITH UNSET(c, 'connectorId', 'recordGroupId') IN @@c",
        {"@c": CollectionNames.CODE_FILES.value, "org": org})
    await g.http_client.execute_aql(
        "FOR g IN @@g FILTER g.orgId == @org REPLACE g WITH UNSET(g, 'repoKey') IN @@g", {"@g": CollectionNames.RECORD_GROUPS.value, "org": org})


async def _one_call(g: IGraphDBProvider, org: str, connector: str, world: dict, counter: world_of.RowCounter) -> int:
    before = counter.rows
    with for_org(org, job="code-path-scope-test"):
        await world_of.get_neighbour_impl(graph_provider=g, connector_id=connector, org_id=org, user_id=world["user_id"], repo="acme/repo3",
                                          file_path=world_of.PATH, qualified_name="function:main", direction="any", depth=1)
    return counter.rows - before


async def test_the_scoped_lookup_answers_as_the_org_wide_one_and_reads_the_same_rows_at_10_and_200_repos(arango, monkeypatch) -> None:
    counter = world_of.RowCounter(arango)
    counter.install(monkeypatch)
    reads: dict[str, dict[int, int]] = {"org-wide": {}, "scoped": {}}
    for repos in (10, 200):
        org, connector = _new_org()
        with for_org(org, job="code-path-scope-test"):
            world = await world_of.write_world(arango, org, connector, repos)
            ops._SCOPED_ORGS.clear()
            assert not await migration.scope_ready(arango, org)
            before = await world_of.answers(arango, org, connector, world)
        reads["org-wide"][repos] = await _one_call(arango, org, connector, world, counter)
        await world_of.backfill(arango, LOG)
        with for_org(org, job="code-path-scope-test"):
            assert await migration.scope_ready(arango, org)
            after = await world_of.answers(arango, org, connector, world)
        reads["scoped"][repos] = await _one_call(arango, org, connector, world, counter)
        assert after == before
        assert before["acme/repo3"].get("neighbors") is not None and "error" not in before["acme/repo3"], before["acme/repo3"]
        assert before["None"].get("raised") == "AmbiguousRepoError", before["None"]       # 10 or 200 repos hold the path: the ambiguity is reported
    assert reads["scoped"][200] == reads["scoped"][10], reads
    assert reads["org-wide"][200] > reads["org-wide"][10], reads              # the old lookup read every repo's file at the path


async def test_rows_written_before_the_scope_are_filled_and_a_backfill_cut_short_leaves_no_marker(arango, monkeypatch) -> None:
    org, connector = _new_org()
    with for_org(org, job="code-path-scope-test"):
        world = await world_of.write_world(arango, org, connector, 5)
    await _strip_scope(arango, org)
    ops._SCOPED_ORGS.clear()
    with for_org(org, job="code-path-scope-test"):
        before = await world_of.answers(arango, org, connector, world)

    real = arango.backfill_code_path_scope
    calls = {"n": 0}

    async def cut_short(limit: int = 5000) -> int:
        calls["n"] += 1
        if calls["n"] > 1:
            raise RuntimeError("stopped mid-backfill")
        return await real(limit=3)
    monkeypatch.setattr(arango, "backfill_code_path_scope", cut_short)
    with pytest.raises(RuntimeError):
        await world_of.backfill(arango, LOG)
    with for_org(org, job="code-path-scope-test"):
        assert not await migration.scope_ready(arango, org)
        assert await world_of.answers(arango, org, connector, world) == before      # half-filled rows: still the org-wide lookup
    monkeypatch.setattr(arango, "backfill_code_path_scope", real)

    assert await world_of.backfill(arango, LOG) > 0
    with for_org(org, job="code-path-scope-test"):
        assert await migration.scope_ready(arango, org)
        files = {d["_key"]: d for d in await arango.get_nodes_by_field_in("codeFiles", "_key", world["records"], raise_on_error=True)}
        groups = {d["_key"]: d for d in await arango.get_nodes_by_field_in("recordGroups", "_key", [g for pair in world["groups"] for g in pair],
                                                                           raise_on_error=True)}
        assert {(d["connectorId"], d["recordGroupId"]) for d in files.values()} == {(connector, code) for _, code in world["groups"]}
        assert {groups[top]["repoKey"] for top, _ in world["groups"]} == {f"repo{i}" for i in range(5)}
        assert {groups[code]["repoKey"] for _, code in world["groups"]} == {"code repository"}
        assert await world_of.answers(arango, org, connector, world) == before


async def test_the_lookup_indexes_exist(arango) -> None:
    def fields(indexes: list[dict]) -> set[tuple[str, ...]]:
        return {tuple(i.get("fields", ())) for i in indexes}
    assert ("orgId", "filePath", "connectorId", "recordGroupId") in fields(await arango.http_client.get_indexes(CollectionNames.CODE_FILES.value))
    groups = fields(await arango.http_client.get_indexes(CollectionNames.RECORD_GROUPS.value))
    assert {("orgId", "repoKey"), ("orgId", "connectorId", "parentExternalGroupId")} <= groups
