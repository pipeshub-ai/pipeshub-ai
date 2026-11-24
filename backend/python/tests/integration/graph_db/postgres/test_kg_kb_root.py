"""A knowledge base's root listed with its candidates from the typed graph (``kg.kb_root``), a folder listed from its children's rows there (``kg.kb_folder``) and the trash
read from kg's rows (``kg.trash``) equal the walk's, after every mutation.

Random collections of folders and files (PARENT_CHILD and ATTACHMENT relations, a few second parents, names that differ only in case, a record of another organisation, one with
no belongs-to edge) are stored, copied into kg and promoted; then records are deleted and restored, moved to the root and under a folder, renamed, added, given another status,
their file resized or retyped (a file made a folder and back), and a parent node is removed from the graph with its relations left behind. After each step the root, a few
folders and the trash (both views) are listed with the read switch off (the walk) and on: pages, every sort, the search and every filter. The rest of the file breaks one thing at
a time (a change waiting in the log, the switch off, a caller's transaction, a copy of older code, a seeded difference) and checks the plans.
"""
from __future__ import annotations

import json
import random
import uuid
from typing import Any

import pytest

from app.services.graph_db.postgres import kb_listings
from app.services.graph_db.postgres.kg import hierarchy as kg_hier
from app.services.graph_db.postgres.kg import kb_folder as kg_kb_folder
from app.services.graph_db.postgres.kg import kb_root as kg_kb_root
from app.services.graph_db.postgres.kg.model import CODE_EPOCH

from .pg_support import new_org
from .test_kg_derivation import ctx, edge
from .test_kg_hierarchy import Tally, _scans
from .test_kg_read import _admin, _switch, flip

pytestmark = [pytest.mark.integration, pytest.mark.timeout(1800)]
KB = "kb1"
FOLDER = "application/vnd.folder"
NAMES = ["alpha", "Alpha", "beta", "Beta", "gamma", "Zed", "_x", "a b", "Ünï", "delta"]


@pytest.fixture
def tally(monkeypatch: pytest.MonkeyPatch) -> Tally:
    t = Tally()
    monkeypatch.setattr(kg_hier, "HIERARCHY", t)
    return t


def _doc(org: str, key: str, i: int, folder: bool, rnd: random.Random, **extra: Any) -> dict:  # noqa: ANN401
    return {"_key": key, "recordName": rnd.choice(NAMES) + str(i % 3), "connectorId": KB, "orgId": org, "recordType": "FILE" if not folder else "FOLDER",
            "origin": rnd.choice(["UPLOAD", "CONNECTOR"]), "mimeType": FOLDER if folder else rnd.choice(["text/plain", "application/pdf"]),
            "externalRecordId": f"x-{key}", "indexingStatus": rnd.choice(["COMPLETED", "QUEUED", None]), "connectorName": rnd.choice([None, "GMAIL"]),
            "createdAtTimestamp": rnd.randrange(5), "updatedAtTimestamp": rnd.randrange(5), **extra}


async def _add(p, org: str, rnd: random.Random, keys: list[str], folders: set[str], i0: int = 0, *, belongs: bool = True, **extra: Any) -> None:  # noqa: ANN401
    with ctx(org):
        await p.batch_upsert_nodes([_doc(org, k, i0 + i, k in folders, rnd, **extra) for i, k in enumerate(keys)], "records")
        await p.batch_upsert_nodes([{"_key": f"f-{k}", "isFile": k not in folders, "mimeType": "text/plain", "sizeInBytes": rnd.randrange(4), "orgId": org} for k in keys], "files")
        if belongs:
            await p.batch_create_edges([edge(k, "records", KB, "apps") for k in keys], "belongsTo")
        await p.batch_create_edges([edge(k, "records", f"f-{k}", "files") for k in keys], "isOfType")


async def _world(p, db, org: str, seed: int, n: int = 16) -> tuple[list[str], set[str]]:
    """A forest of ``n`` records of kb1 (each with at most one parent of a lower index, a few with a second), a third of them folders, and two that the root never lists from
    kb1: one of another organisation and one with no belongs-to edge to it. kb2 holds a record of its own."""
    rnd = random.Random(seed)
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": a, "name": a.upper(), "type": "KB", "orgId": org} for a in (KB, "kb2")], "apps")
        await p.batch_upsert_nodes([{"_key": "u1", "email": "u1@example.com", "fullName": "U One", "orgId": org}], "users")
    records = [f"r{i:02d}" for i in range(n)]
    folders = {r for r in records if rnd.random() < 0.35}
    await _add(p, org, rnd, records, folders)
    await _add(p, org, rnd, ["alien"], set(), orgId="another-org")
    await _add(p, org, rnd, ["loose"], set(), belongs=False)
    with ctx(org):
        await p.batch_upsert_nodes([_doc(org, "other", 0, False, rnd, connectorId="kb2")], "records")
        await p.batch_create_edges([edge("other", "records", "kb2", "apps")], "belongsTo")
        rels = {}
        for i in range(1, n):
            for _ in range(rnd.choice([0, 1, 1, 2])):
                rels[(records[rnd.randrange(i)], records[i])] = rnd.choice(["PARENT_CHILD", "PARENT_CHILD", "ATTACHMENT"])
        if rels:
            await p.batch_create_edges([edge(a, "records", b, "records", relationshipType=t) for (a, b), t in rels.items()], "nodeRelations")
    assert (await flip(db, org))[0] == 2
    return records, folders


FOLDER_READS = 16       # the reads of one folder by _listing


async def _listing(p, org: str, folders: tuple[str, ...] | list[str] = ()) -> dict[str, Any]:
    """The root and each of ``folders`` read every way the API reads them: pages across the folders and the records, every sort both ways, the search, each filter; kb2 and a
    missing collection; and the trash, both views."""
    out: dict[str, Any] = {}
    with ctx(org):
        for f in folders:
            for skip, limit in ((0, 50), (1, 2), (3, 2)):
                out[f"{f} page {skip} {limit}"] = await p.get_folder_children(KB, f, skip, limit)
            for sort in ("name", "created_at", "updated_at", "size"):
                for way in ("asc", "desc"):
                    out[f"{f} sort {sort} {way}"] = await p.get_folder_children(KB, f, 0, 3, sort_by=sort, sort_order=way)
            out[f"{f} search"] = await p.get_folder_children(KB, f, 0, 50, search="ALP")
            out[f"{f} search ext"] = await p.get_folder_children(KB, f, 0, 50, search="x-r1")
            out[f"{f} filters"] = await p.get_folder_children(KB, f, 0, 50, record_types=["FILE"], origins=["UPLOAD"])
            out[f"{f} filters 2"] = await p.get_folder_children(KB, f, 0, 50, connectors=["GMAIL", "KNOWLEDGE_BASE"], indexing_status=["COMPLETED"])
            out[f"{f} filters 3"] = await p.get_folder_children(KB, f, 0, 50, connectors=["GMAIL"], sort_by="size", sort_order="desc")
        for files in (False, True):
            out[f"trash {files}"] = await p.list_trashed_records(KB, org, skip=0, limit=26, single_file_batches_only=files, count_cap=10000)
        for skip, limit in ((0, 50), (0, 2), (1, 2), (3, 2), (5, 3), (40, 5)):
            out[f"page {skip} {limit}"] = await p.get_kb_children(KB, skip, limit)
        for sort in ("name", "created_at", "updated_at", "size"):
            for way in ("asc", "desc"):
                out[f"sort {sort} {way}"] = await p.get_kb_children(KB, 1, 4, sort_by=sort, sort_order=way)
        out["search"] = await p.get_kb_children(KB, 0, 50, search="ALP")
        out["search ext"] = await p.get_kb_children(KB, 0, 50, search="x-r0")
        out["filters"] = await p.get_kb_children(KB, 0, 50, record_types=["FILE"], origins=["UPLOAD"])
        out["filters 2"] = await p.get_kb_children(KB, 0, 50, connectors=["GMAIL"], indexing_status=["COMPLETED"])
        out["kb2"] = await p.get_kb_children("kb2", 0, 50)
        out["missing"] = await p.get_kb_children("nope", 0, 50)
    return out


async def _both(p, org: str, folders: tuple[str, ...] | list[str] = ()) -> tuple[dict, dict]:
    with _switch("0"):
        walk = await _listing(p, org, folders)
    return walk, await _listing(p, org, folders)


async def _rels(db, org: str, kind: str) -> list[tuple[str, str]]:
    with ctx(org):
        rows = await db.run(lambda tx: tx.fetch("""SELECT from_id, to_id FROM graph.edges WHERE org_id = $1 AND coll = 'nodeRelations' AND doc->>'relationshipType' = $2
            ORDER BY from_id, to_id""", org, kind))
    return [(r["from_id"], r["to_id"]) for r in rows]


@pytest.mark.parametrize("seed", range(8))
async def test_after_every_mutation_the_root_from_kg_equals_the_walk(pg_provider, pg_db, tally, seed) -> None:
    org = new_org()
    rnd = random.Random(2000 + seed)
    records, folders = await _world(pg_provider, pg_db, org, seed)
    batches: list[str] = []
    steps = ["delete", "to_root", "under", "rename", "status", "restore", "add", "resize", "drop_parent", "retype", "delete", "to_root", "under", "retype"]
    for n, step in enumerate(steps):
        if step == "delete":
            batch = str(uuid.uuid4())
            with ctx(org):
                await pg_provider.soft_delete_records([rnd.choice(records)], KB, delete_source="USER", batch_id=batch, deleted_by_user_id="u1")
            batches.append(batch)
        elif step == "restore" and batches:
            with ctx(org):
                items = await pg_provider.get_records_in_delete_batch(batches[0], org)
                await pg_provider.restore_records([{"id": i["record"]["id"]} for i in items], batches.pop(0), connector_id=KB)
        elif step == "to_root" and (pc := await _rels(pg_db, org, "PARENT_CHILD")):
            a, b = rnd.choice(pc)
            with ctx(org):
                await pg_provider.delete_edge(a, "records", b, "records", "nodeRelations")
        elif step == "under" and folders:
            with ctx(org):
                await pg_provider.batch_create_edges([edge(rnd.choice(sorted(folders)), "records", rnd.choice(records), "records", relationshipType="PARENT_CHILD")],
                                                     "nodeRelations")
        elif step == "rename":
            with ctx(org):
                await pg_provider.batch_update_nodes([{"id": rnd.choice(records), "recordName": rnd.choice(NAMES)}], "records")
        elif step == "status":
            with ctx(org):
                await pg_provider.batch_update_nodes([{"id": r, "indexingStatus": rnd.choice(["COMPLETED", "FAILED", None])} for r in rnd.sample(records, 3)], "records")
        elif step == "resize":
            with ctx(org):
                await pg_provider.batch_update_nodes([{"id": f"f-{r}", "sizeInBytes": rnd.randrange(6)} for r in rnd.sample(records, 3)], "files")
        elif step == "retype":            # a file made a folder (its file says isFile false) and a folder made a file, their mime types left as they were
            flip_to = rnd.choice(records)
            with ctx(org):
                await pg_provider.batch_update_nodes([{"id": f"f-{flip_to}", "isFile": flip_to in folders}], "files")
            folders ^= {flip_to}
        elif step == "add":
            new = [f"n{n}a", f"n{n}b"]
            await _add(pg_provider, org, rnd, new, {new[0]}, 100 + n)
            with ctx(org):
                await pg_provider.batch_create_edges([edge(new[0], "records", new[1], "records", relationshipType="PARENT_CHILD")], "nodeRelations")
            records += new
            folders.add(new[0])
        elif step == "drop_parent" and (pc := await _rls_parents(pg_db, org)):
            gone = rnd.choice(pc)                     # the node goes, its relations stay: the walk keeps its children off the root
            await _admin("DELETE FROM graph.nodes WHERE org_id = $1 AND coll = 'records' AND id = $2", org, gone)
            records.remove(gone)
            folders.discard(gone)
        await flip(pg_db, org)
        tally.c.clear()
        seen = rnd.sample(sorted(folders), min(3, len(folders)))
        walk, got = await _both(pg_provider, org, seen)
        assert got == walk, step
        # every read but the missing collection's (no connector to serve) is kg's
        assert tally.c[("kb_root", "kg")] == 19 and tally.c[("kb_root", "not_served")] == 1 and not tally.c[("kb_root", "error")], (step, dict(tally.c))
        assert tally.c[("kb_folder", "kg")] == FOLDER_READS * len(seen) and tally.c[("trash", "kg")] == 2, (step, dict(tally.c))
        assert sum(v for (what, why), v in tally.c.items() if what in ("kb_folder", "trash") and why != "off") == FOLDER_READS * len(seen) + 2, (step, dict(tally.c))
    assert any(x["records"] or x["folders"] for x in walk.values() if isinstance(x, dict) and x.get("success")), "the sequence left the root empty: it would prove nothing"


async def _rls_parents(db, org: str) -> list[str]:
    """The records that are a PARENT_CHILD parent of another."""
    return sorted({a for a, _ in await _rels(db, org, "PARENT_CHILD")})


# ---- one thing at a time -----------------------------------------------------------------------------------------------------------------------------------------------------------

TREE = ["a", "b", "c", "d", "e"]


async def _tree(p, db, org: str) -> None:
    """a (folder) > b (folder) > c; a ~attachment~> d; e at the root."""
    rnd = random.Random(7)
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": a, "name": a.upper(), "type": "KB", "orgId": org} for a in (KB, "kb2")], "apps")
    await _add(p, org, rnd, TREE, {"a", "b"})
    with ctx(org):
        await p.batch_create_edges([edge("a", "records", "b", "records", relationshipType="PARENT_CHILD"), edge("b", "records", "c", "records", relationshipType="PARENT_CHILD"),
                                    edge("a", "records", "d", "records", relationshipType="ATTACHMENT")], "nodeRelations")
    await flip(db, org)


def _ids(page: dict) -> list[str]:
    return [x["id"] for x in page["folders"]] + [x["id"] for x in page["records"]]


async def test_the_root_lists_records_with_no_parent_child_parent_record_and_an_attachment_is_one(pg_provider, pg_db, tally) -> None:
    """An attachment (d) has a hierarchy parent but no PARENT_CHILD one: the walk lists it on the root, and so does kg (pc_top, not the primary parent)."""
    org = new_org()
    await _tree(pg_provider, pg_db, org)
    tally.c.clear()
    with ctx(org):
        got = await pg_provider.get_kb_children(KB, 0, 50)
    with _switch("0"), ctx(org):
        assert await pg_provider.get_kb_children(KB, 0, 50) == got
    assert sorted(_ids(got)) == ["a", "d", "e"] and tally.c[("kb_root", "kg")] == 1, dict(tally.c)


async def test_a_change_waiting_in_the_log_takes_the_walk_until_it_is_drained(pg_provider, pg_db, tally, monkeypatch) -> None:
    org = new_org()
    await _tree(pg_provider, pg_db, org)
    monkeypatch.setenv("PG_KG_FLUSH", "async")
    with ctx(org):
        await pg_provider.delete_edge("b", "records", "c", "records", "nodeRelations")
    tally.c.clear()
    with ctx(org):
        assert sorted(_ids(await pg_provider.get_kb_children(KB, 0, 50))) == ["a", "c", "d", "e"]
    assert tally.c[("kb_root", "not_served")] and not tally.c[("kb_root", "kg")], dict(tally.c)
    await flip(pg_db, org)
    tally.c.clear()
    with ctx(org):
        assert sorted(_ids(await pg_provider.get_kb_children(KB, 0, 50))) == ["a", "c", "d", "e"]
    assert tally.c[("kb_root", "kg")], dict(tally.c)


async def test_with_the_switch_off_or_in_a_callers_transaction_or_inside_a_folder_kg_is_not_read(pg_provider, pg_db, tally) -> None:
    org = new_org()
    await _tree(pg_provider, pg_db, org)
    tally.c.clear()
    with _switch("0"), ctx(org):
        assert (await pg_provider.get_kb_children(KB, 0, 50))["totalCount"] == 3
    with ctx(org):
        tid = await pg_provider.begin_transaction(read=[], write=["records"])
        try:
            assert (await pg_provider.get_kb_children(KB, 0, 50, transaction=tid))["totalCount"] == 3
        finally:
            await pg_provider.rollback_transaction(tid)
        assert (await pg_provider.get_kb_children(KB, 0, 50))["totalCount"] == 3
        assert _ids(await pg_provider.get_folder_children(KB, "a", 0, 50)) == ["b"]           # a folder's listing is kg's too
        tid = await pg_provider.begin_transaction(read=[], write=["records"])
        try:
            assert _ids(await pg_provider.get_folder_children(KB, "a", 0, 50, transaction=tid)) == ["b"]
        finally:
            await pg_provider.rollback_transaction(tid)
    with _switch("0"), ctx(org):
        assert _ids(await pg_provider.get_folder_children(KB, "a", 0, 50)) == ["b"]
    assert tally.c[("kb_root", "off")] == 2 and tally.c[("kb_root", "kg")] == 1 and tally.c[("kb_folder", "kg")] == 1 and tally.c[("kb_folder", "off")] == 2, dict(tally.c)
    assert sum(tally.c.values()) == 6, dict(tally.c)


async def test_a_connector_copied_by_older_projection_code_takes_the_walk(pg_provider, pg_db, tally) -> None:
    """Code of epoch 9 wrote no pc_top: a connector it verified is not served (its rows would name no root)."""
    org = new_org()
    await _tree(pg_provider, pg_db, org)
    await _admin("UPDATE kg.projection_state SET code_epoch = 9 WHERE org_id = $1 AND app_sk <> 0", org)
    tally.c.clear()
    with ctx(org):
        assert sorted(_ids(await pg_provider.get_kb_children(KB, 0, 50))) == ["a", "d", "e"]
    assert tally.c[("kb_root", "not_served")] and not tally.c[("kb_root", "kg")], dict(tally.c)


async def test_a_copy_of_epoch_9_code_is_filled_in_place_by_the_bootstrap_and_stays_enforced_and_served(pg_provider, pg_db, tally, caplog) -> None:
    """The upgrade path: a connector copied, verified and enforced by epoch 9 code (pc_top false on every row, kg_0024's default). The bootstrap fills pc_top from the copy's
    own links and relabels it: still enforced and served, never demoted or copied again, and the root is kg's and the walk's. A page bound leaves the fill unfinished and the
    copy unserved until the next pass."""
    import logging

    from app.services.graph_db.postgres.kg import bootstrap
    org = new_org()
    await _tree(pg_provider, pg_db, org)

    async def tops(tx: Any) -> dict:  # noqa: ANN401
        return {r["id"]: r["pc_top"] for r in await tx.fetch("SELECT id, pc_top FROM kg.records WHERE org_id = $1", org)}
    with ctx(org):
        derived = await pg_db.run(tops)
    assert {k for k, v in derived.items() if v} == {"a", "d", "e"}
    await _admin("UPDATE kg.records SET pc_top = false WHERE org_id = $1", org)               # what epoch 9 code copied
    await _admin("UPDATE kg.projection_state SET code_epoch = 9 WHERE org_id = $1 AND app_sk <> 0", org)
    caplog.set_level(logging.WARNING, logger=bootstrap.logger.name)
    await bootstrap.bring_to_enforce(pg_db, org, {})
    with ctx(org):
        assert await pg_db.run(tops) == derived
        state = await pg_db.run(lambda tx: tx.fetch("""SELECT k.id, s.mode, s.code_epoch FROM kg.projection_state s JOIN kg.node_keys k ON k.org_id = s.org_id AND k.sk = s.app_sk
            AND k.coll = 1 WHERE s.org_id = $1 ORDER BY k.id""", org))
    assert [tuple(r) for r in state] == [(KB, "enforce", CODE_EPOCH), ("kb2", "enforce", CODE_EPOCH)]
    assert not [r for r in caplog.records if r.name == bootstrap.logger.name], [r.getMessage() for r in caplog.records]
    tally.c.clear()
    walk, got = await _both(pg_provider, org)
    assert got == walk and sorted(_ids(walk["page 0 50"])) == ["a", "d", "e"]
    assert tally.c[("kb_root", "kg")] == 19 and tally.c[("kb_root", "not_served")] == 1, dict(tally.c)          # the missing collection's is the walk's


async def test_a_fill_page_reads_at_most_a_page_of_rows_and_relabels_on_the_last(pg_provider, pg_db) -> None:
    from app.services.graph_db.postgres.kg import backfill as bf
    org = new_org()
    await _tree(pg_provider, pg_db, org)
    await _admin("UPDATE kg.records SET pc_top = false WHERE org_id = $1", org)
    await _admin("UPDATE kg.projection_state SET code_epoch = 9 WHERE org_id = $1 AND app_sk <> 0", org)

    async def epoch(tx: Any) -> int:  # noqa: ANN401
        return await tx.fetchval("""SELECT s.code_epoch FROM kg.projection_state s JOIN kg.node_keys k ON k.org_id = s.org_id AND k.sk = s.app_sk AND k.coll = 1
            WHERE s.org_id = $1 AND k.id = $2""", org, KB)
    mode, more = await bf.fill_pc_tops(pg_db, org, KB, page=2, max_pages=1)
    assert mode is None and isinstance(more, int)
    with ctx(org):
        assert await pg_db.run(epoch) == bf.TOPS_FROM
    assert await bf.fill_pc_tops(pg_db, org, KB, page=2, after=more) == ("enforce", None)
    with ctx(org):
        assert await pg_db.run(epoch) == bf.LISTING_FROM                          # the knowledge-base listing's fill takes it on from there
    assert await bf.fill_kb_listing(pg_db, org, KB, page=2) == ("enforce", None)
    with ctx(org):
        assert await pg_db.run(epoch) == CODE_EPOCH
        assert sorted(_ids(await pg_provider.get_kb_children(KB, 0, 50))) == ["a", "d", "e"]


async def test_a_record_moved_writes_pc_top_and_a_rename_writes_none(pg_provider, pg_db, monkeypatch) -> None:
    """pc_top is written only where it changes: renaming a record writes none, taking c out of b writes c's row, putting it back writes it again."""
    from app.services.graph_db.postgres.kg import writers
    org = new_org()
    await _tree(pg_provider, pg_db, org)
    written: list[tuple] = []
    real = writers.KgWriter.pc_top_set

    async def spy(self, rows: list[tuple]) -> int:
        written.extend(rows)
        return await real(self, rows)
    monkeypatch.setattr(writers.KgWriter, "pc_top_set", spy)
    with ctx(org):
        await pg_provider.batch_update_nodes([{"id": "c", "recordName": "C2"}], "records")
    await flip(pg_db, org)
    assert written == []
    with ctx(org):
        await pg_provider.delete_edge("b", "records", "c", "records", "nodeRelations")
    await flip(pg_db, org)
    assert [(r[2], r[4]) for r in written] == [("c", True)]
    written.clear()
    with ctx(org):
        await pg_provider.batch_create_edges([edge("b", "records", "c", "records", relationshipType="PARENT_CHILD")], "nodeRelations")
    await flip(pg_db, org)
    assert [(r[2], r[4]) for r in written] == [("c", False)]


async def test_older_projection_code_cannot_write_into_kg_any_more(pg_provider, pg_db) -> None:
    import asyncpg
    org = new_org()
    await _tree(pg_provider, pg_db, org)
    with ctx(org), pytest.raises(asyncpg.exceptions.InvalidParameterValueError):
        await pg_db.run(lambda tx: tx.fetchval("SELECT kg.w_changes_ack($1)", 9))


async def test_a_sampled_root_that_differs_from_the_walk_takes_the_connector_back_to_the_walk(pg_provider, pg_db, monkeypatch) -> None:
    """The safety net: e's pc_top changed in kg alone; with every read sampled, kg lists no e, the walk does, the difference demotes the connector."""
    from app.services.graph_db.postgres.kg import net
    org = new_org()
    await _tree(pg_provider, pg_db, org)
    await _admin("UPDATE kg.records SET pc_top = false WHERE org_id = $1 AND coll = 3 AND id = 'e'", org)
    monkeypatch.setenv(net.SAMPLE_ENV, "1")
    with ctx(org):
        assert sorted(_ids(await pg_provider.get_kb_children(KB, 0, 50))) == ["a", "d"]          # kg answered, from the seeded row
        await net.settle()
        mode = await pg_db.run(lambda tx: tx.fetchval("""SELECT s.mode FROM kg.projection_state s JOIN kg.node_keys k ON k.org_id = s.org_id AND k.sk = s.app_sk
            WHERE s.org_id = $1 AND k.coll = 1 AND k.id = $2""", org, KB))
        assert mode == "legacy"
        assert sorted(_ids(await pg_provider.get_kb_children(KB, 0, 50))) == ["a", "d", "e"]     # the walk's, from now on


async def test_the_root_is_answered_by_index_searches(pg_provider, pg_db) -> None:
    """EXPLAIN gate: 3,000 records of the knowledge base in 30 folders beside its root, never analysed and analysed: the candidates are read from records_pc_tops, and no
    statement of the page scans a table (each candidate, its relations, its file and each page folder's children are read by their keys)."""
    org = new_org()
    await _tree(pg_provider, pg_db, org)
    await _admin("""INSERT INTO graph.nodes (org_id, coll, id, doc) SELECT $1::text, 'records', 'plan-' || g, jsonb_build_object('_key', 'plan-' || g, 'id', 'plan-' || g, 'orgId', $1::text,
          'connectorId', $2::text, 'recordName', 'P' || g, 'mimeType', CASE WHEN g <= 30 THEN 'application/vnd.folder' ELSE 'text/plain' END) FROM generate_series(1, 3000) g""", org, KB)
    await _admin("""INSERT INTO graph.edges (org_id, coll, from_coll, from_id, to_coll, to_id) SELECT $1, 'belongsTo', 'records', 'plan-' || g, 'apps', $2
          FROM generate_series(1, 3000) g""", org, KB)
    await _admin("""INSERT INTO graph.edges (org_id, coll, from_coll, from_id, to_coll, to_id, ekey, doc) SELECT $1, 'nodeRelations', 'records', 'plan-' || (1 + g % 30), 'records',
          'plan-' || g, 'PARENT_CHILD', '{"relationshipType": "PARENT_CHILD"}' FROM generate_series(31, 3000) g""", org)
    await flip(pg_db, org)
    with ctx(org):
        app = await pg_db.run(lambda tx: tx.fetchval("SELECT app_sk FROM kg.records WHERE org_id = $1 AND coll = 3 AND id = 'e'", org))
    for state in ("fresh", "analysed"):
        if state == "analysed":
            await _admin("ANALYZE kg.records; ANALYZE graph.nodes; ANALYZE graph.edges")

        async def go(tx) -> tuple[list[str], str, list[str]]:
            await tx.fetchval(kg_hier.PROFILE)
            ran = await tx.fetchval("EXPLAIN (ANALYZE, FORMAT JSON) " + kg_kb_root.TOPS, org, app)
            tops = (json.loads(ran) if isinstance(ran, str) else ran)[0]["Plan"]
            ids = [r["id"] for r in await tx.fetch(kg_kb_root.TOPS, org, app)]
            bad = []
            for sql, args in ((kg_kb_root.FOLDERS, (org, KB, kb_listings.FOLDER_MIME_TYPES, None, 0, 20, kb_listings._KB, ids)),
                              (kg_kb_root.RECORDS, (org, KB, kb_listings.FOLDER_MIME_TYPES, None, None, None, None, None, 4, True, 0, 20, kb_listings._KB, ids))):
                ran = await tx.fetchval("EXPLAIN (ANALYZE, FORMAT JSON) " + sql, *args)
                bad += _scans((json.loads(ran) if isinstance(ran, str) else ran)[0]["Plan"])
            # the candidates are records_pc_tops bounded by the connector (its range is the root), which the shared checker does not name as a key
            return [x for x in _scans(tops) if "'records_pc_tops'" not in x], json.dumps(tops), bad
        with ctx(org):
            bad_tops, plan, bad = await pg_db.run(go, custom_plan=True)
        assert '"Index Name": "records_pc_tops' in plan and not bad_tops, f"{state}: {bad_tops} {plan}"
        assert not bad, f"{state}: {bad}"
        with ctx(org):
            got = await pg_provider.get_kb_children(KB, 0, 20)
        assert got["counts"]["totalFolders"] == 31 and got["counts"]["totalRecords"] == 2
        assert next(f for f in got["folders"] if f["id"] == "plan-1")["counts"]["totalItems"] == 99


# ---- a folder and the trash from kg's knowledge-base listing columns (kg_0025) ------------------------------------------------------------------------------------------------------

async def test_a_status_or_a_file_change_writes_the_listing_columns_of_that_record_alone(pg_provider, pg_db, monkeypatch) -> None:
    """A status flip is a change of the record now (before kg_0025 it logged nothing): its projection writes the record's listing columns and nothing more; a write that changes
    none of them (a rename) writes none; a resize of the file it is typed as writes that record's."""
    from app.services.graph_db.postgres.kg import writers
    org = new_org()
    await _tree(pg_provider, pg_db, org)
    written: list[tuple] = []
    real = writers.KgWriter.kb_listing_set

    async def spy(self, rows: list[tuple]) -> int:
        written.extend(rows)
        return await real(self, rows)
    monkeypatch.setattr(writers.KgWriter, "kb_listing_set", spy)
    with ctx(org):
        await pg_provider.batch_update_nodes([{"id": "c", "recordName": "C2"}], "records")
    await flip(pg_db, org)
    assert written == []
    with ctx(org):
        await pg_provider.batch_update_nodes([{"id": "c", "indexingStatus": "FAILED"}], "records")
    await flip(pg_db, org)
    assert [(r[2], r[4]) for r in written] == [("c", "FAILED")]
    written.clear()
    with ctx(org):
        await pg_provider.batch_update_nodes([{"id": "f-c", "sizeInBytes": 77}], "files")
    await flip(pg_db, org)
    assert [(r[2], r[8]) for r in written] == [("c", 77)]
    with ctx(org):
        page = await pg_provider.get_folder_children(KB, "b", 0, 50, indexing_status=["FAILED"])
    assert [(r["id"], r["indexingStatus"], r["fileRecord"]["sizeInBytes"]) for r in page["records"]] == [("c", "FAILED", 77)]


async def test_a_copy_of_epoch_10_code_is_filled_in_place_by_the_bootstrap_and_stays_enforced_and_served(pg_provider, pg_db, tally, caplog) -> None:
    """The upgrade path of kg_0025: a connector copied, verified and enforced by epoch 10 code holds no listing columns. The bootstrap fills them from the graph and relabels the
    copy: still enforced, never copied again, and a folder and the trash are kg's and the walk's. Until then the connector is not served."""
    import logging

    from app.services.graph_db.postgres.kg import bootstrap
    org = new_org()
    await _tree(pg_provider, pg_db, org)
    with ctx(org):
        await pg_provider.soft_delete_records(["e"], KB, delete_source="USER", batch_id="bt-e", deleted_by_user_id="u1")
    await flip(pg_db, org)

    async def cols(tx: Any) -> dict:
        return {r["id"]: tuple(r)[1:] for r in await tx.fetch("""SELECT id, indexing_status, connector_name, doc_created_ms, doc_updated_ms, file_size, file_kind, kb_odd
            FROM kg.records WHERE org_id = $1 ORDER BY id""", org)}
    with ctx(org):
        derived = await pg_db.run(cols)
    assert derived["e"][5] == 1 and derived["a"][5] == 0 and derived[KB][5] is None
    await _admin("""UPDATE kg.records SET indexing_status = NULL, connector_name = NULL, doc_created_ms = NULL, doc_updated_ms = NULL, file_size = NULL, file_kind = NULL, kb_odd = false
        WHERE org_id = $1""", org)                                                                     # what epoch 10 code copied
    await _admin("UPDATE kg.projection_state SET code_epoch = 10 WHERE org_id = $1 AND app_sk <> 0", org)
    tally.c.clear()
    with ctx(org):
        assert _ids(await pg_provider.get_folder_children(KB, "a", 0, 50)) == ["b"]
    assert tally.c[("kb_folder", "not_served")] == 1, dict(tally.c)
    caplog.set_level(logging.WARNING, logger=bootstrap.logger.name)
    await bootstrap.bring_to_enforce(pg_db, org, {})
    with ctx(org):
        assert await pg_db.run(cols) == derived
        state = await pg_db.run(lambda tx: tx.fetch("""SELECT k.id, s.mode, s.code_epoch FROM kg.projection_state s JOIN kg.node_keys k ON k.org_id = s.org_id AND k.sk = s.app_sk
            AND k.coll = 1 WHERE s.org_id = $1 ORDER BY k.id""", org))
    assert [tuple(r) for r in state] == [(KB, "enforce", CODE_EPOCH), ("kb2", "enforce", CODE_EPOCH)]
    assert not [r for r in caplog.records if r.name == bootstrap.logger.name], [r.getMessage() for r in caplog.records]
    tally.c.clear()
    walk, got = await _both(pg_provider, org, ["a", "b"])
    assert got == walk and _ids(walk["a page 0 50"]) == ["b"] and walk["trash True"]["total"] == 1
    assert tally.c[("kb_folder", "kg")] == 2 * FOLDER_READS and tally.c[("trash", "kg")] == 2, dict(tally.c)


async def test_a_folder_with_a_child_kg_cannot_hold_or_of_another_connector_takes_the_walk(pg_provider, pg_db, tally) -> None:
    """A child typed as two files (kb_odd) or of another connector (whose rows may wait in its own log) is the walk's; so is a folder kg holds no row for."""
    org = new_org()
    await _tree(pg_provider, pg_db, org)
    with ctx(org):
        await pg_provider.batch_upsert_nodes([{"_key": "f-c2", "isFile": True, "sizeInBytes": 1, "orgId": org}], "files")
        await pg_provider.batch_create_edges([edge("c", "records", "f-c2", "files")], "isOfType")
    await flip(pg_db, org)
    tally.c.clear()
    walk_free = await _listing_of(pg_provider, org, "b")
    assert tally.c[("kb_folder", "odd")] == 1 and not tally.c[("kb_folder", "kg")], dict(tally.c)
    with _switch("0"):
        assert await _listing_of(pg_provider, org, "b") == walk_free
    with ctx(org):
        await pg_provider.delete_edge("c", "records", "f-c2", "files", "isOfType")
        await pg_provider.batch_upsert_nodes([_doc(org, "o1", 1, False, random.Random(1), connectorId="kb2")], "records")
        await pg_provider.batch_create_edges([edge("b", "records", "o1", "records", relationshipType="PARENT_CHILD")], "nodeRelations")
    await flip(pg_db, org)
    tally.c.clear()
    got = await _listing_of(pg_provider, org, "b")
    with _switch("0"):
        assert await _listing_of(pg_provider, org, "b") == got
    assert sorted(x["id"] for x in got["records"]) == ["c", "o1"] and tally.c[("kb_folder", "odd")] == 1, dict(tally.c)
    tally.c.clear()
    with ctx(org):
        assert (await pg_provider.get_folder_children(KB, "nope", 0, 50))["totalCount"] == 0
    assert tally.c[("kb_folder", "unknown")] == 1, dict(tally.c)


async def _listing_of(p, org: str, folder: str) -> dict:
    with ctx(org):
        return await p.get_folder_children(KB, folder, 0, 50)


async def test_a_folder_is_answered_by_index_searches(pg_provider, pg_db) -> None:
    """EXPLAIN gate: a folder of 3,000 children (30 subfolders) beside 3,000 records elsewhere, never analysed and analysed: its children are a range of the children index and
    its links, and every other read of the page (each page folder's children, each page row's node and file) is by key."""
    org = new_org()
    await _tree(pg_provider, pg_db, org)
    for parent, base in (("a", 0), ("e", 3000)):          # e's children: rows a folder read must not touch
        await _admin("""INSERT INTO graph.nodes (org_id, coll, id, doc) SELECT $1::text, 'records', 'plan-' || g, jsonb_build_object('_key', 'plan-' || g, 'id', 'plan-' || g,
              'orgId', $1::text, 'connectorId', $2::text, 'recordName', 'P' || g, 'indexingStatus', 'COMPLETED', 'createdAtTimestamp', g) FROM generate_series($3::int + 1, $3::int + 3000) g""",
                     org, KB, base)
        await _admin("""INSERT INTO graph.nodes (org_id, coll, id, doc) SELECT $1::text, 'files', 'pf-' || g, jsonb_build_object('_key', 'pf-' || g, 'isFile', g % 100 > 0, 'sizeInBytes', g)
              FROM generate_series($2::int + 1, $2::int + 3000) g""", org, base)
        await _admin("""INSERT INTO graph.edges (org_id, coll, from_coll, from_id, to_coll, to_id) SELECT $1, 'isOfType', 'records', 'plan-' || g, 'files', 'pf-' || g
              FROM generate_series($2::int + 1, $2::int + 3000) g""", org, base)
        await _admin("""INSERT INTO graph.edges (org_id, coll, from_coll, from_id, to_coll, to_id, ekey, doc) SELECT $1, 'nodeRelations', 'records', $3::text, 'records',
              'plan-' || g, 'PARENT_CHILD', '{"relationshipType": "PARENT_CHILD"}' FROM generate_series($2::int + 1, $2::int + 3000) g""", org, base, parent)
    await flip(pg_db, org)
    for state in ("fresh", "analysed"):
        if state == "analysed":
            await _admin("ANALYZE kg.records; ANALYZE kg.links; ANALYZE graph.nodes; ANALYZE graph.edges")

        async def go(tx) -> list[str]:
            await tx.fetchval(kg_hier.PROFILE)
            await tx.fetchval(kg_hier.APP_KEY, org, KB)
            sk, app, is_dir = await kg_kb_folder.folder(tx, org, KB, "a")
            bad = []
            for sql, args in ((kg_kb_folder.FOLDERS, (org, sk, app, None, 0, 20, is_dir)),
                              (kg_kb_folder.RECORDS_PAGE, (org, sk, app, None, None, None, None, ["COMPLETED"], 4, False, 0, 20))):
                ran = await tx.fetchval("EXPLAIN (ANALYZE, FORMAT JSON) " + sql, *args)
                bad += _scans((json.loads(ran) if isinstance(ran, str) else ran)[0]["Plan"])
            return bad
        with ctx(org):
            bad = await pg_db.run(go, custom_plan=True)
        assert not bad, f"{state}: {bad}"
        with ctx(org):
            got = await pg_provider.get_folder_children(KB, "a", 0, 20, sort_by="size", sort_order="desc")
        assert got["counts"]["folders"] == 31 and got["counts"]["records"] == 2970 and [r["id"] for r in got["records"]] == []        # b and every hundredth: folders
        with ctx(org):
            got = await pg_provider.get_folder_children(KB, "a", 40, 3, sort_by="size", sort_order="desc")
        assert [r["id"] for r in got["records"]] == [f"plan-{g}" for g in sorted((g for g in range(1, 3001) if g % 100), reverse=True)][9:12]
