"""A connector's trash listing paged from the typed graph (``kg.trash``) equals the walk's, after every mutation.

Random collections of folders, files and attachments are stored, copied into kg and promoted; then a sequence of deletes (single items and multi-selects, a folder with what is
below it), restores, deletes again and tied delete times is applied, and after each step the trash is listed with the read switch off (the walk) and on: every page by cursor at
several page sizes, page numbers with an offset, the count at several caps, for the full listing and the single-file one. The rest of the file breaks one thing at a time
(a batch deleted at two times, a change waiting in the log, the switch off, a caller's transaction, a seeded difference) and checks the plans.
"""
from __future__ import annotations

import json
import random
import re
import uuid
from typing import Any

import pytest

from app.services.graph_db.postgres.kg import hierarchy as kg_hier
from app.services.graph_db.postgres.kg import trash as kg_trash
from app.services.graph_db.postgres.kg.model import CODE_EPOCH
from tests.support import plan_gate

from .pg_support import new_org
from .test_kg_derivation import ctx, edge
from .test_kg_hierarchy import Tally, _scans
from .test_kg_read import _admin, _switch, flip

pytestmark = [pytest.mark.integration, pytest.mark.timeout(1800)]
KB = "kb1"
FOLDER = "application/vnd.folder"


@pytest.fixture
def tally(monkeypatch: pytest.MonkeyPatch) -> Tally:
    t = Tally()
    monkeypatch.setattr(kg_hier, "HIERARCHY", t)
    return t


async def _world(p, db, org: str, records: list[str], relations: list[tuple[str, str, str]], files: set[str]) -> None:
    """``records`` of the collection (those in ``files`` typed as files, the rest as folders), ``relations`` (parent, child, relationship type) between them, a user who deletes."""
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": a, "name": a, "type": "KB", "orgId": org} for a in (KB, "kb2")], "apps")
        await p.batch_upsert_nodes([{"_key": "u1", "email": "u1@example.com", "fullName": "U One", "orgId": org}], "users")
        await p.batch_upsert_nodes([{"_key": r, "recordName": r.upper(), "connectorId": KB, "orgId": org, "recordType": "FILE", "origin": "UPLOAD",
                                     "mimeType": "text/plain" if r in files else FOLDER} for r in records], "records")
        await p.batch_upsert_nodes([{"_key": f"f-{r}", "isFile": r in files, "mimeType": "text/plain", "sizeInBytes": 10 + i, "orgId": org}
                                    for i, r in enumerate(records)], "files")
        await p.batch_create_edges([edge(r, "records", KB, "apps") for r in records], "belongsTo")
        await p.batch_create_edges([edge(r, "records", f"f-{r}", "files") for r in records], "isOfType")
        if relations:
            await p.batch_create_edges([edge(a, "records", b, "records", relationshipType=t) for a, b, t in relations], "nodeRelations")
    assert (await flip(db, org))[0] == 2


def _random(seed: int, n: int = 14) -> tuple[list[str], list[tuple[str, str, str]], set[str]]:
    """A forest: each record has at most one parent of a lower index (a few have a second), so a delete takes a subtree."""
    rnd = random.Random(seed)
    records = [f"r{i:02d}" for i in range(n)]
    rels = {}
    for i in range(1, n):
        for _ in range(rnd.choice([0, 1, 1, 1, 2])):
            rels[(records[rnd.randrange(i)], records[i])] = rnd.choice(["PARENT_CHILD", "PARENT_CHILD", "ATTACHMENT"])
    return records, [(a, b, t) for (a, b), t in rels.items()], {r for r in records if rnd.random() < 0.6}


async def _listing(p, org: str) -> dict[str, Any]:
    """Every way the trash is read: each page by cursor at three page sizes, page numbers with an offset, the count at three caps; full and single-file listings."""
    out: dict[str, Any] = {}
    with ctx(org):
        for files_only in (False, True):
            for limit in (1, 2, 50):
                pages, after = [], None
                while True:
                    got = await p.list_trashed_records(KB, org, limit=limit, single_file_batches_only=files_only, after=after, count_cap=3)
                    pages.append(got)
                    if len(got["items"]) < limit:
                        break
                    after = (got["items"][-1]["batchDeletedAt"], got["items"][-1]["batchId"])
                out[f"cursor {files_only} {limit}"] = pages
            for skip in (0, 1, 3):
                out[f"skip {files_only} {skip}"] = await p.list_trashed_records(KB, org, skip=skip, limit=2, single_file_batches_only=files_only)
            for cap in (0, 1, None):
                out[f"cap {files_only} {cap}"] = (await p.list_trashed_records(KB, org, limit=1, single_file_batches_only=files_only, count_cap=cap))["total"]
    return out


async def _both(p, org: str) -> tuple[dict, dict]:
    with _switch("0"):
        walk = await _listing(p, org)
    return walk, await _listing(p, org)


async def _trash(p, org: str, ids: list[str]) -> str:
    batch = str(uuid.uuid4())
    with ctx(org):
        await p.soft_delete_records(ids, KB, delete_source="USER", batch_id=batch, deleted_by_user_id="u1")
    return batch


async def _restore(p, org: str, batch: str) -> None:
    with ctx(org):
        items = await p.get_records_in_delete_batch(batch, org)
        assert await p.restore_records([{"id": i["record"]["id"]} for i in items], batch, connector_id=KB)


async def _live(p, org: str, records: list[str]) -> list[str]:
    with ctx(org):
        rows = await p._db.run(lambda tx: tx.fetch("""SELECT id FROM graph.nodes WHERE org_id = $1 AND coll = 'records' AND id = ANY($2::text[])
            AND NOT coalesce((doc->>'isDeleted')::boolean, false) ORDER BY id""", org, records))
    return [r["id"] for r in rows]


@pytest.mark.parametrize("seed", range(8))
async def test_after_every_mutation_the_trash_from_kg_equals_the_walk(pg_provider, pg_db, tally, seed) -> None:
    org = new_org()
    rnd = random.Random(1000 + seed)
    records, rels, files = _random(seed)
    await _world(pg_provider, pg_db, org, records, rels, files)
    batches: list[str] = []
    steps = ["delete", "delete", "multi", "tie", "restore", "delete", "multi", "restore", "delete"]
    for step in steps:
        live = await _live(pg_provider, org, records)
        if step in ("delete", "multi") and live:
            batches.append(await _trash(pg_provider, org, rnd.sample(live, min(len(live), 1 if step == "delete" else rnd.randint(2, 3)))))
        elif step == "tie" and len(batches) >= 2:
            await _admin("""UPDATE graph.nodes SET doc = jsonb_set(doc, '{deletedAtTimestamp}', to_jsonb(1790000000000::bigint))
                WHERE org_id = $1 AND coll = 'records' AND doc->>'deleteBatchId' = ANY($2::text[])""", org, batches[-2:])
        elif step == "restore" and batches:
            await _restore(pg_provider, org, batches.pop(rnd.randrange(len(batches))))
        await flip(pg_db, org)
        tally.c.clear()
        walk, got = await _both(pg_provider, org)
        assert got == walk, step
        assert tally.c[("trash", "kg")] and not tally.c[("trash", "odd")], (step, dict(tally.c))
    assert any(p["items"] for p in walk["cursor False 50"]), "the sequence left the trash empty: it would prove nothing"


# ---- one thing at a time -----------------------------------------------------------------------------------------------------------------------------------------------------------

TREE = ["a", "b", "c", "d", "e"]
TREE_RELS = [("a", "b", "PARENT_CHILD"), ("b", "c", "PARENT_CHILD"), ("a", "d", "ATTACHMENT")]


async def _tree(p, db, org: str) -> None:
    await _world(p, db, org, TREE, TREE_RELS, {"c", "d", "e"})


async def test_a_batch_deleted_at_two_times_takes_the_walk(pg_provider, pg_db, tally) -> None:
    """The walk sorts a batch by its roots' latest time; kg cannot when the records of one batch were deleted at different times, so it hands the read to the walk."""
    org = new_org()
    await _tree(pg_provider, pg_db, org)
    await _trash(pg_provider, org, ["b"])                                   # b with c below it: one batch, one time
    await _trash(pg_provider, org, ["e"])
    await _admin("""UPDATE graph.nodes SET doc = jsonb_set(doc, '{deletedAtTimestamp}', to_jsonb((doc->>'deletedAtTimestamp')::bigint + 7))
        WHERE org_id = $1 AND coll = 'records' AND id = 'c'""", org)
    await flip(pg_db, org)
    tally.c.clear()
    walk, got = await _both(pg_provider, org)
    assert got == walk and walk["cursor False 50"][0]["total"] == 2
    assert tally.c[("trash", "odd")], dict(tally.c)          # every read that reaches b; one that stops before it (a count capped at 0, a page of one) is kg's


async def test_a_change_waiting_in_the_log_takes_the_walk_until_it_is_drained(pg_provider, pg_db, tally, monkeypatch) -> None:
    org = new_org()
    await _tree(pg_provider, pg_db, org)
    monkeypatch.setenv("PG_KG_FLUSH", "async")
    await _trash(pg_provider, org, ["e"])
    with ctx(org):
        assert [i["record"]["id"] for i in (await pg_provider.list_trashed_records(KB, org))["items"]] == ["e"]
    assert tally.c[("trash", "not_served")] and not tally.c[("trash", "kg")], dict(tally.c)


async def test_with_the_switch_off_or_in_a_callers_transaction_kg_is_not_read(pg_provider, pg_db, tally) -> None:
    org = new_org()
    await _tree(pg_provider, pg_db, org)
    await _trash(pg_provider, org, ["e"])
    await flip(pg_db, org)
    tally.c.clear()
    with _switch("0"), ctx(org):
        assert (await pg_provider.list_trashed_records(KB, org))["total"] == 1
    with ctx(org):
        tid = await pg_provider.begin_transaction(read=[], write=["records"])
        try:
            assert (await pg_provider.list_trashed_records(KB, org, transaction=tid))["total"] == 1
        finally:
            await pg_provider.rollback_transaction(tid)
        assert (await pg_provider.list_trashed_records(KB, org))["total"] == 1
    assert tally.c[("trash", "off")] == 2 and tally.c[("trash", "kg")] == 1, dict(tally.c)


async def test_a_connector_copied_by_older_projection_code_takes_the_walk(pg_provider, pg_db, tally) -> None:
    """Code of epoch 7 wrote no trash columns: a connector it verified is not served (its trashed records would have no place in the listing)."""
    org = new_org()
    await _tree(pg_provider, pg_db, org)
    await _trash(pg_provider, org, ["e"])
    await flip(pg_db, org)
    await _admin("UPDATE kg.projection_state SET code_epoch = 7 WHERE org_id = $1 AND app_sk <> 0", org)
    tally.c.clear()
    walk, got = await _both(pg_provider, org)
    assert got == walk and walk["cursor False 50"][0]["total"] == 1
    assert tally.c[("trash", "not_served")] and not tally.c[("trash", "kg")], dict(tally.c)


async def test_a_copy_of_epoch_7_code_is_filled_in_place_by_the_bootstrap_and_stays_enforced_and_served(pg_provider, pg_db, tally, caplog) -> None:
    """The upgrade path: a connector copied, verified and enforced by epoch 7 code (no trash columns) holds trashed records and documents whose delete fields only Python
    reads as it does (a number past a double, one below it, a boolean batch). The bootstrap fills the columns in place exactly as the projection derives them and relabels
    the copy: still enforced and served, never demoted or copied again, and the listing is kg's and the walk's."""
    import logging

    from app.services.graph_db.postgres.kg import bootstrap
    org = new_org()
    await _tree(pg_provider, pg_db, org)
    await _admin("""UPDATE graph.nodes SET doc = doc || jsonb_build_object('deletedAtTimestamp', '1e999999', 'deleteBatchId', true) WHERE org_id = $1 AND coll = 'records' AND id = 'a'""", org)
    await _admin("""UPDATE graph.nodes SET doc = doc || jsonb_build_object('deletedAtTimestamp', '1e-20000') WHERE org_id = $1 AND coll = 'records' AND id = 'd'""", org)
    await _trash(pg_provider, org, ["b"])
    await _trash(pg_provider, org, ["e"])
    await flip(pg_db, org)

    async def columns(tx: Any) -> dict:  # noqa: ANN401
        return {r["id"]: (r["deleted_ms"], r["delete_batch"]) for r in await tx.fetch("SELECT id, deleted_ms, delete_batch FROM kg.records WHERE org_id = $1", org)}
    with ctx(org):
        derived = await pg_db.run(columns)                                          # what this code's projection derives
    assert derived["a"] == (None, "True") and derived["d"] == (0, None) and derived["c"][1] == derived["b"][1]
    await _admin("UPDATE kg.records SET deleted_ms = NULL, delete_batch = NULL WHERE org_id = $1", org)               # what epoch 7 code copied
    await _admin("UPDATE kg.projection_state SET code_epoch = 7 WHERE org_id = $1 AND app_sk <> 0", org)
    caplog.set_level(logging.WARNING, logger=bootstrap.logger.name)
    await bootstrap.bring_to_enforce(pg_db, org, {})
    with ctx(org):
        assert await pg_db.run(columns) == derived
        state = await pg_db.run(lambda tx: tx.fetch("""SELECT k.id, s.mode, s.code_epoch FROM kg.projection_state s JOIN kg.node_keys k ON k.org_id = s.org_id AND k.sk = s.app_sk
            AND k.coll = 1 WHERE s.org_id = $1 ORDER BY k.id""", org))
    assert [tuple(r) for r in state] == [(KB, "enforce", CODE_EPOCH), ("kb2", "enforce", CODE_EPOCH)]
    assert not [r for r in caplog.records if r.name == bootstrap.logger.name], [r.getMessage() for r in caplog.records]
    tally.c.clear()
    walk, got = await _both(pg_provider, org)
    assert got == walk and walk["cursor False 50"][0]["total"] == 2
    assert tally.c[("trash", "kg")] and not tally.c[("trash", "not_served")], dict(tally.c)


async def test_a_fill_page_reads_at_most_a_page_of_records_even_when_none_carries_a_delete(pg_provider, pg_db) -> None:
    """A page of the fill is a page of the connector's records in id order, whatever they hold: on a connector with no deleted record a page of two scans two records and
    goes on past them, without relabelling the copy; the last, short page relabels it."""
    from app.services.graph_db.postgres.kg import backfill as bf
    org = new_org()
    await _tree(pg_provider, pg_db, org)
    await flip(pg_db, org)
    await _admin("UPDATE kg.projection_state SET code_epoch = 7 WHERE org_id = $1 AND app_sk <> 0", org)

    async def epoch(tx: Any) -> int:  # noqa: ANN401
        return await tx.fetchval("""SELECT s.code_epoch FROM kg.projection_state s JOIN kg.node_keys k ON k.org_id = s.org_id AND k.sk = s.app_sk AND k.coll = 1
            WHERE s.org_id = $1 AND k.id = $2""", org, KB)
    assert await bf.fill_trash(pg_db, org, KB, page=2, max_pages=1) == (None, "b")         # a and b scanned, neither carries a delete
    with ctx(org):
        assert await pg_db.run(epoch) == 7
    assert await bf.fill_trash(pg_db, org, KB, page=2, after="b") == ("enforce", None)
    with ctx(org):
        assert await pg_db.run(epoch) == bf.TOPS_FROM                                     # the pc_top fill takes it to this code's epoch


def _relabel_sql() -> str:
    """kg_0019's relabel of copies labelled epoch 8 or later (the revision's own text, read without importing the migration package)."""
    import ast
    from pathlib import Path
    tree = ast.parse((Path(__file__).resolve().parents[5] / "db" / "versions" / "kg" / "0019_trash.py").read_text(encoding="utf-8"))
    return next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign) and any(getattr(t, "id", None) == "RELABEL" for t in n.targets))


async def _trash_columns(db: Any, org: str) -> dict:  # noqa: ANN401
    with ctx(org):
        return await db.run(lambda tx: _rows(tx, org))


async def _rows(tx: Any, org: str) -> dict:  # noqa: ANN401
    return {r["id"]: (r["deleted_ms"], r["delete_batch"]) for r in await tx.fetch("SELECT id, deleted_ms, delete_batch FROM kg.records WHERE org_id = $1 AND coll = 3", org)}


async def test_a_filled_copy_taken_down_to_kg_0018_and_up_again_is_filled_again_before_it_is_served(pg_provider, pg_db, tally) -> None:
    """A copy filled and enforced at epoch 8, after a downgrade to kg_0018 and an upgrade back: the columns are new and empty, and kg_0019 relabels the copy epoch 7. The
    bootstrap fills it again: enforced and served, and its listing is the walk's (before the relabel it was served with empty columns and listed nothing)."""
    from app.services.graph_db.postgres.kg import bootstrap
    org = new_org()
    await _tree(pg_provider, pg_db, org)
    await _trash(pg_provider, org, ["b"])
    await _trash(pg_provider, org, ["e"])
    await flip(pg_db, org)
    filled = await _trash_columns(pg_db, org)
    await _admin("UPDATE kg.records SET deleted_ms = NULL, delete_batch = NULL WHERE org_id = $1", org)            # the columns as an upgrade brings them back
    await _admin(_relabel_sql())
    await bootstrap.bring_to_enforce(pg_db, org, {})
    assert await _trash_columns(pg_db, org) == filled
    tally.c.clear()
    walk, got = await _both(pg_provider, org)
    assert got == walk and walk["cursor False 50"][0]["total"] == 2
    assert tally.c[("trash", "kg")], dict(tally.c)


async def test_a_fill_unit_retried_after_its_writes_reads_the_same_page_again(pg_provider, pg_db, monkeypatch) -> None:
    """A retryable failure after a page's writes and before its commit (the pre-commit hook): the unit is run again from the same cursor, so no page is skipped and the
    relabelled copy holds every trashed record's columns."""
    from app.core.db.errors import SerializationConflict
    from app.services.graph_db.postgres.kg import backfill as bf
    from app.services.graph_db.postgres.kg import writers
    org = new_org()
    await _tree(pg_provider, pg_db, org)
    await _trash(pg_provider, org, ["b"])                   # b and c, in two pages of two (a b | c d | e)
    await _trash(pg_provider, org, ["e"])
    await flip(pg_db, org)
    filled = await _trash_columns(pg_db, org)
    await _admin("UPDATE kg.records SET deleted_ms = NULL, delete_batch = NULL WHERE org_id = $1", org)
    await _admin("UPDATE kg.projection_state SET code_epoch = 7 WHERE org_id = $1 AND app_sk <> 0", org)
    armed = {"wrote": False, "failed": False}
    real_set, real_hook = writers.KgWriter.trash_set, pg_db.pre_commit_hook

    async def spy(self: Any, rows: list) -> int:  # noqa: ANN401
        armed["wrote"] = armed["wrote"] or bool(rows)
        return await real_set(self, rows)

    async def hook(tx: Any) -> None:  # noqa: ANN401
        if real_hook is not None:
            await real_hook(tx)
        if armed["wrote"] and not armed["failed"]:
            armed["failed"] = True
            raise SerializationConflict("injected after the page's writes")
    monkeypatch.setattr(writers.KgWriter, "trash_set", spy)
    monkeypatch.setattr(pg_db, "pre_commit_hook", hook)
    assert await bf.fill_trash(pg_db, org, KB, page=2) == ("enforce", None)
    assert armed["failed"]
    assert await _trash_columns(pg_db, org) == filled


async def test_a_single_file_listing_takes_the_walk_while_another_connector_of_the_org_is_not_served(pg_provider, pg_db, tally) -> None:
    """One delete batch across two connectors: the walk counts both records and lists no single file for it. While the other connector's copy is not served (here an epoch 7
    copy with no trash columns yet), kg cannot see its record of the batch, so the single-file listing is the walk's; with every connector served, a genuine one-record batch
    is still kg's."""
    org = new_org()
    await _tree(pg_provider, pg_db, org)
    with ctx(org):
        await pg_provider.batch_upsert_nodes([{"_key": "f", "recordName": "F", "connectorId": "kb2", "orgId": org, "recordType": "FILE", "origin": "UPLOAD",
                                               "mimeType": "text/plain"}], "records")
        await pg_provider.batch_upsert_nodes([{"_key": "f-f", "isFile": True, "mimeType": "text/plain", "sizeInBytes": 1, "orgId": org}], "files")
        await pg_provider.batch_create_edges([edge("f", "records", "kb2", "apps")], "belongsTo")
        await pg_provider.batch_create_edges([edge("f", "records", "f-f", "files")], "isOfType")
    await _trash(pg_provider, org, ["d"])                                                   # a genuine single file
    await flip(pg_db, org)
    tally.c.clear()
    with _switch("0"), ctx(org):
        walk = await pg_provider.list_trashed_records(KB, org, single_file_batches_only=True)
    with ctx(org):
        assert await pg_provider.list_trashed_records(KB, org, single_file_batches_only=True) == walk
    assert [i["record"]["id"] for i in walk["items"]] == ["d"] and tally.c[("trash", "kg")], dict(tally.c)
    batch = await _trash(pg_provider, org, ["e"])
    with ctx(org):
        await pg_provider.soft_delete_records(["f"], "kb2", delete_source="USER", batch_id=batch, deleted_by_user_id="u1")
    await flip(pg_db, org)
    await _admin("""UPDATE kg.records SET deleted_ms = NULL, delete_batch = NULL WHERE org_id = $1 AND app_sk = (SELECT app_sk FROM kg.records WHERE org_id = $1 AND coll = 3 AND id = 'f')""", org)
    await _admin("""UPDATE kg.projection_state s SET code_epoch = 7 FROM kg.node_keys k WHERE s.org_id = $1 AND k.org_id = s.org_id AND k.sk = s.app_sk AND k.coll = 1 AND k.id = 'kb2'""", org)
    tally.c.clear()
    with _switch("0"), ctx(org):
        walk = await pg_provider.list_trashed_records(KB, org, single_file_batches_only=True)
    with ctx(org):
        assert await pg_provider.list_trashed_records(KB, org, single_file_batches_only=True) == walk
    assert [i["record"]["id"] for i in walk["items"]] == ["d"] and tally.c[("trash", "org")] and not tally.c[("trash", "kg")], dict(tally.c)


async def test_the_walk_reads_a_page_and_its_total_in_one_snapshot(pg_provider, pg_db, monkeypatch) -> None:
    """With no caller transaction the walk's page and total are one repeatable-read unit: a restore committed between the two statements changes neither."""
    from app.core.db import uow
    org = new_org()
    await _tree(pg_provider, pg_db, org)
    await _trash(pg_provider, org, ["e"])
    real = uow.Tx.fetch

    async def fetch(self: Any, sql: str, *args: Any) -> Any:  # noqa: ANN401
        out = await real(self, sql, *args)
        if "pipeshub:graph.list_trashed_records */" in sql:
            await _admin("UPDATE graph.nodes SET doc = jsonb_set(doc, '{isDeleted}', 'false') WHERE org_id = $1 AND coll = 'records' AND id = 'e'", org)
        return out
    monkeypatch.setattr(uow.Tx, "fetch", fetch)
    with _switch("0"), ctx(org):
        got = await pg_provider.list_trashed_records(KB, org)
    assert [i["record"]["id"] for i in got["items"]] == ["e"] and got["total"] == 1


async def test_a_live_records_projection_writes_no_trash_columns(pg_provider, pg_db, monkeypatch) -> None:
    """The trash columns are written only where they change: renaming a live record writes none, trashing one writes its row, restoring it clears it."""
    from app.services.graph_db.postgres.kg import writers
    org = new_org()
    await _tree(pg_provider, pg_db, org)
    written: list[tuple] = []
    real = writers.KgWriter.trash_set

    async def spy(self, rows: list[tuple]) -> int:
        written.extend(rows)
        return await real(self, rows)
    monkeypatch.setattr(writers.KgWriter, "trash_set", spy)
    with ctx(org):
        await pg_provider.batch_update_nodes([{"id": "e", "recordName": "E2"}], "records")
    await flip(pg_db, org)
    assert written == []
    batch = await _trash(pg_provider, org, ["e"])
    await flip(pg_db, org)
    assert [(r[2], r[5]) for r in written] == [("e", batch)]
    written.clear()
    await _restore(pg_provider, org, batch)
    await flip(pg_db, org)
    assert [(r[2], r[4], r[5]) for r in written] == [("e", None, None)]


async def test_a_slow_trash_read_hands_over_to_the_walk(pg_provider, pg_db, tally, monkeypatch) -> None:
    """A statement past the read's limit (here a count made slow on purpose under a 1 ms limit) is the walk's read, counted as slow, not an error."""
    org = new_org()
    await _tree(pg_provider, pg_db, org)
    await _trash(pg_provider, org, ["e"])
    await flip(pg_db, org)
    monkeypatch.setattr(kg_trash, "LIMITS", "SELECT set_config('statement_timeout', '1ms', true)")
    monkeypatch.setattr(kg_trash, "COUNT", kg_trash.COUNT.replace("SELECT count(*) FILTER", "SELECT pg_sleep(0.05), count(*) FILTER"))
    tally.c.clear()
    with ctx(org):
        assert [i["record"]["id"] for i in (await pg_provider.list_trashed_records(KB, org))["items"]] == ["e"]
    assert tally.c[("trash", "slow")] and not tally.c[("trash", "kg")] and not tally.c[("trash", "error")], dict(tally.c)


async def test_older_projection_code_cannot_write_into_kg_any_more(pg_provider, pg_db) -> None:
    import asyncpg
    org = new_org()
    await _tree(pg_provider, pg_db, org)
    with ctx(org), pytest.raises(asyncpg.exceptions.InvalidParameterValueError):
        await pg_db.run(lambda tx: tx.fetchval("SELECT kg.w_changes_ack($1)", 7))


async def test_a_sampled_listing_that_differs_from_the_walk_takes_the_connector_back_to_the_walk(pg_provider, pg_db, monkeypatch) -> None:
    """The safety net: e's delete batch changed in kg alone; with every read sampled, kg lists nothing for it, the walk lists e, the difference demotes the connector."""
    from app.services.graph_db.postgres.kg import net
    org = new_org()
    await _tree(pg_provider, pg_db, org)
    await _trash(pg_provider, org, ["e"])
    await flip(pg_db, org)
    await _admin("UPDATE kg.records SET deleted_ms = NULL WHERE org_id = $1 AND coll = 3 AND id = 'e'", org)
    monkeypatch.setenv(net.SAMPLE_ENV, "1")
    with ctx(org):
        assert (await pg_provider.list_trashed_records(KB, org))["items"] == []        # kg answered, from the seeded row
        await net.settle()
        mode = await pg_db.run(lambda tx: tx.fetchval("""SELECT s.mode FROM kg.projection_state s JOIN kg.node_keys k ON k.org_id = s.org_id AND k.sk = s.app_sk
            WHERE s.org_id = $1 AND k.coll = 1 AND k.id = $2""", org, KB))
        assert mode == "legacy"
        assert [i["record"]["id"] for i in (await pg_provider.list_trashed_records(KB, org))["items"]] == ["e"]     # the walk's, from now on


async def test_every_trash_statement_is_answered_by_index_searches(pg_provider, pg_db, monkeypatch) -> None:
    """EXPLAIN gate: 3,000 trashed records of the connector in kg and in the graph, beside 3,000 live ones, never analysed and analysed: no statement scans a table, the batches
    are read from records_trash and each batch's records from records_trash_batch, and a page's rows read each parent by its to_id."""
    org = new_org()
    await _tree(pg_provider, pg_db, org)
    await _trash(pg_provider, org, ["e"])
    await flip(pg_db, org)
    await _admin("""INSERT INTO graph.nodes (org_id, coll, id, doc) SELECT $1::text, 'records', 'plan-' || g, jsonb_build_object('_key', 'plan-' || g, 'orgId', $1::text, 'connectorId', $2::text,
          'recordName', 'P' || g, 'isDeleted', g % 2 = 0, 'deletedAtTimestamp', 1790000000000 + g / 4, 'deleteBatchId', 'pb-' || (g / 4)) FROM generate_series(1, 6000) g""", org, KB)
    await _admin("""INSERT INTO kg.records (org_id, sk, coll, id, kind, app_sk, path, leaf, deleted, deleted_ms, delete_batch)
        SELECT $1, 73000000 + g, 3, 'plan-' || g, 3, a.app_sk, a.path, true, g % 2 = 0, 1790000000000 + g / 4, 'pb-' || (g / 4) FROM generate_series(1, 6000) g,
          (SELECT app_sk, path FROM kg.records WHERE org_id = $1 AND coll = 3 AND id = 'e') a""", org)
    await _admin("""INSERT INTO graph.edges (org_id, coll, from_coll, from_id, to_coll, to_id, doc) SELECT $1, 'nodeRelations', 'records', 'plan-' || (g - 1), 'records', 'plan-' || g,
          '{"relationshipType": "PARENT_CHILD"}' FROM generate_series(2, 6000) g""", org)
    await _admin("DELETE FROM kg.graph_changes WHERE org_id = $1", org)        # what the rows above log is in kg already: page() reads the connector from kg
    with ctx(org):
        app = await pg_db.run(lambda tx: tx.fetchval("SELECT app_sk FROM kg.records WHERE org_id = $1 AND coll = 3 AND id = 'e'", org))
    cases = {"page": (kg_trash.PAGE, [app, KB, org, False, None, None, 26, 25]), "page_after": (kg_trash.PAGE, [app, KB, org, True, 1790000000700, "pb-700", 26, 25]),
             "count": (kg_trash.COUNT, [app, KB, org, False, None, None, 10001])}
    for state in ("fresh", "analysed"):
        if state == "analysed":
            await _admin("ANALYZE kg.records; ANALYZE graph.nodes; ANALYZE graph.edges")
        for name, (sql, args) in cases.items():
            async def go(tx, sql=sql, args=args) -> tuple[list[str], str]:
                await tx.fetchval(kg_hier.PROFILE)
                ran = await tx.fetchval("EXPLAIN (ANALYZE, FORMAT JSON) " + sql, org, *args)
                plan = (json.loads(ran) if isinstance(ran, str) else ran)[0]["Plan"]
                # the batches are records_trash read backwards from the cursor, one row per step (LIMIT 1), and a batch's records are records_trash_batch bounded by the batch
                # (a key the shared checker does not name)
                return [x for x in _scans(plan) if "'records_trash'" not in x and "(delete_batch >= b.batch) AND (delete_batch <= b.batch)" not in x], json.dumps(plan)
            with ctx(org):
                bad, plan = await pg_db.run(go, custom_plan=True)
            assert not bad, f"{state} {name}: {bad}"
            used = set(re.findall(r'"Index Name": "[^"]*?(records_trash(?:_batch)?)"', plan))
            assert used == {"records_trash", "records_trash_batch"}, f"{state} {name}: {used}"

        # a page's rows, as page() runs them under the profile: a record's first parent is a seek on to_id, not the primary key read in from_id order (the plan that
        # needs no sort, which reads the org's relations up to the parent)
        async def rows(tx) -> list[str]:
            await tx.fetchval(kg_hier.PROFILE)
            got, _ = await kg_trash.page(tx, org, KB, org, files_only=False, skip=0, limit=26, after=None, count_cap=None)
            ran = got[0]["QUERY PLAN"]
            plan = (json.loads(ran) if isinstance(ran, str) else ran)[0]["Plan"]
            # every edge read is bounded by an endpoint (a sequential or bitmap read included); the parent read must not be the primary key in from_id order
            loose = plan_gate.violations(plan, {"edges": r"\b(to_id|from_id) (=|>=) "})
            return loose + [f"{n['Relation Name']}: {plan_gate.bounds(n)}" for n in plan_gate.scans(plan, "edges")
                            if "nodeRelations" in str(plan_gate.bounds(n)) and str(n.get("Index Name")).endswith("_pkey")]
        with ctx(org), monkeypatch.context() as m:
            m.setattr(kg_trash, "ROWS", "EXPLAIN (ANALYZE, FORMAT JSON) " + kg_trash.ROWS)
            bad = await pg_db.run(rows, custom_plan=True)
        assert not bad, f"{state} rows: {bad}"
