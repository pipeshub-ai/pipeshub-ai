"""A code path is looked up within its connector and repo on ArangoDB: the neighbour tool answers exactly as the org-wide lookup did, under every
way a repo is named and with none (the ambiguity report), and reads the same few documents whether 10 or 200 repos hold the path. Rows written
before the scope existed are filled by the start-up backfill, which a crash leaves unmarked and a second run finishes.

Needs ARANGO_IT_URL (see tests/integration/real_graph.py); without it the tests skip.
"""
from __future__ import annotations

import logging
import os
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
# The backfill reads and marks every org of the database: a database per xdist worker keeps a run from marking another's orgs.
ARANGO_DB = f"code_path_scope_it_{os.environ.get('PYTEST_XDIST_WORKER', 'main')}"


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

    async def cut_short(limit: int = 5000, *, org_id: str | None = None) -> int:
        calls["n"] += 1
        if calls["n"] > 1:
            raise RuntimeError("stopped mid-backfill")
        return await real(limit=3, org_id=org_id)
    monkeypatch.setattr(arango, "backfill_code_path_scope", cut_short)
    await world_of.backfill(arango, LOG)      # the org's failure is logged; it stays unmarked
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


@pytest.mark.parametrize("how", ["batch_update_nodes", "update_node", "batch_upsert_nodes"])
async def test_a_code_record_moved_to_another_repo_is_found_in_the_new_repo_only(arango, how) -> None:
    """The scoped lookup reads the code file's copy of its record's group: every write that moves the record moves the copy with it."""
    org, connector = _new_org()
    with for_org(org, job="code-path-scope-test"):
        world = await world_of.write_world(arango, org, connector, 5)
    await world_of.backfill(arango, LOG)
    moved, new_group = world["records"][3], world["groups"][4][1]
    with for_org(org, job="code-path-scope-test"):
        if how == "batch_update_nodes":
            await arango.batch_update_nodes([{"id": moved, "recordGroupId": new_group}], "records")
        elif how == "update_node":
            await arango.update_node(moved, "records", {"recordGroupId": new_group})
        else:
            doc = await arango.get_document(moved, "records")
            await arango.batch_upsert_nodes([{**doc, "recordGroupId": new_group}], "records")
        scoped = {repo: await world_of.records_at(arango, org, connector, world, repo) for repo in ("acme/repo3", "acme/repo4")}
    assert scoped == {"acme/repo3": [], "acme/repo4": sorted([moved, world["records"][4]])}, scoped
    ops._SCOPED_ORGS.clear()
    await arango.remove_sync_point(migration.marker_key(org), CollectionNames.SYNC_POINTS.value)     # the org-wide lookup, as the oracle
    with for_org(org, job="code-path-scope-test"):
        assert not await migration.scope_ready(arango, org)
        assert {repo: await world_of.records_at(arango, org, connector, world, repo) for repo in ("acme/repo3", "acme/repo4")} == scoped


async def test_a_write_of_other_records_does_no_code_file_work(arango, monkeypatch) -> None:
    """A batch of other records is the writer's own request alone: no statement touches codeFiles and no transaction is opened."""
    org, connector = _new_org()
    from app.config.constants.arangodb import Connectors, OriginTypes
    from app.models.entities import Record, RecordType
    files = [Record(org_id=org, record_name=f"f{i}.txt", record_type=RecordType.FILE, external_record_id=f"ext-file-{uuid.uuid4().hex[:8]}", version=1,
                    origin=OriginTypes.CONNECTOR, connector_name=Connectors.GITHUB, connector_id=connector, record_group_id="g1").to_arango_base_record()
             for i in range(20)]
    seen: list[str] = []
    real_aql, real_begin = arango.http_client.execute_aql, arango.http_client.begin_transaction

    async def aql(query, *a, **k) -> object:
        seen.append(query)
        return await real_aql(query, *a, **k)

    async def begin(*a, **k):  # noqa: ANN202
        seen.append("BEGIN")
        return await real_begin(*a, **k)
    monkeypatch.setattr(arango.http_client, "execute_aql", aql)
    monkeypatch.setattr(arango.http_client, "begin_transaction", begin)
    with for_org(org, job="code-path-scope-test"):
        await arango.batch_upsert_nodes(files, "records")
    assert not [q for q in seen if "code_files" in q or q == "BEGIN"], seen


async def test_a_batch_that_updates_nothing_while_rows_remain_does_not_end_the_backfill(arango, monkeypatch) -> None:
    await world_of.early_zero_backfill(arango, LOG, monkeypatch, lambda org: _strip_scope(arango, org))


async def test_a_partial_upsert_naming_no_type_moves_a_code_files_scope(arango) -> None:
    """An existing code record upserted with only its new group (no recordType): the copy reads the stored type and follows the move."""
    org, connector = _new_org()
    with for_org(org, job="code-path-scope-test"):
        world = await world_of.write_world(arango, org, connector, 2)
        rid, new_group = world["records"][0], world["groups"][1][1]
        await arango.batch_upsert_nodes([{"_key": rid, "orgId": org, "recordGroupId": new_group}], "records")
        assert (await arango.get_document(rid, "codeFiles"))["recordGroupId"] == new_group


async def test_an_upsert_of_code_records_and_its_scope_copy_commit_together(arango, monkeypatch) -> None:
    """With no caller transaction the record upsert and the code-file copy are two requests: they run in one stream transaction, so a copy
    that fails takes the record write back with it, and no writer can commit between them."""
    org, connector = _new_org()
    with for_org(org, job="code-path-scope-test"):
        world = await world_of.write_world(arango, org, connector, 2)
        rid, old_group, new_group = world["records"][0], world["groups"][0][1], world["groups"][1][1]
        doc = await arango.get_document(rid, "records")

        async def fail(keys: list[str], transaction: str | None) -> None:
            raise RuntimeError("the copy failed")
        monkeypatch.setattr(arango, "_sync_code_file_scope", fail)
        with pytest.raises(RuntimeError):
            await arango.batch_upsert_nodes([{**doc, "recordGroupId": new_group}], "records")
        monkeypatch.undo()
        assert (await arango.get_document(rid, "records"))["recordGroupId"] == old_group
        assert (await arango.get_document(rid, "codeFiles"))["recordGroupId"] == old_group


@pytest.mark.parametrize("refusal", ["conflict", "narrow schema"])
async def test_an_upsert_of_code_records_whose_transaction_is_refused_is_run_again(arango, monkeypatch, refusal) -> None:
    """A write conflict (errorNum 1200) or a narrow schema's refusal inside the owned transaction rolls it back whole; the upsert and the
    copy are run again in a new one, as a write outside a transaction is."""
    from app.exceptions.graph_db_exceptions import RecordSchemaNarrowError
    org, connector = _new_org()
    with for_org(org, job="code-path-scope-test"):
        world = await world_of.write_world(arango, org, connector, 2)
        rid, new_group = world["records"][0], world["groups"][1][1]
        doc = await arango.get_document(rid, "records")
        real, calls = arango._sync_code_file_scope, {"n": 0}

        async def refused_once(keys: list[str], transaction: str | None) -> None:
            calls["n"] += 1
            if calls["n"] == 1:
                raise RecordSchemaNarrowError("refused") if refusal == "narrow schema" else RuntimeError("[1200] write-write conflict")
            await real(keys, transaction)
        monkeypatch.setattr(arango, "_sync_code_file_scope", refused_once)
        await arango.batch_upsert_nodes([{**doc, "recordGroupId": new_group}], "records")
        assert calls["n"] == 2
        assert (await arango.get_document(rid, "records"))["recordGroupId"] == new_group
        assert (await arango.get_document(rid, "codeFiles"))["recordGroupId"] == new_group


async def test_an_external_org_is_filled_and_marked(arango) -> None:
    await world_of.external_org_is_marked(arango, LOG, lambda org: _strip_scope(arango, org))


async def test_an_org_with_leftovers_does_not_keep_another_org_unmarked(arango, monkeypatch) -> None:
    await world_of.org_with_leftovers_does_not_block_another(arango, LOG, monkeypatch, lambda org: _strip_scope(arango, org))
