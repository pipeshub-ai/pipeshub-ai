"""Upkeep of the vector tables: storage of the point columns, build sizing, the safe-mode option and nightly, group collection and the
point-count fold. The jobs are driven through ``python -m db.maintain`` (``maintain.main``), as an operator runs them."""

import hashlib
import threading
import time

import psycopg
import pytest
from psycopg import errors as pg
from sqlalchemy import create_engine

from db import maintain
from db.lib import fold, placement, upkeep

from .rls_fixture import ORG_A, ORG_B, ORG_P, uow
from .test_placement import env as placement_env
from .test_retrieval import VEC, adopt, connect_as, put


def jobs_url(cluster, name) -> str:
    return f"postgresql://pipeshub_jobs@127.0.0.1:{cluster.port}/{name}"


def write_terms(c, org: str, point: str, group: str, weights: dict[int, float], coll: int = 1) -> int:
    """A point and its postings, as the provider writes them."""
    sk = c.execute("INSERT INTO retrieval.points (org_id, collection_id, space_id, point_id, group_key, embedding, embedding_dim, lex_terms) "
                   "VALUES (%s, %s, 1, %s, %s, %s::ext.halfvec, 4, %s) RETURNING point_sk", (org, coll, point, group, VEC, list(weights))).fetchone()[0]
    for term, w in weights.items():
        c.execute("INSERT INTO retrieval.postings (org_id, collection_id, space_id, term, point_sk, w) VALUES (%s, %s, 1, %s, %s, %s)",
                  (org, coll, term - 2**32 if term > 2**31 - 1 else term, sk, w))
    return sk


def place(cluster, name, org: str, roots: tuple[str, ...] = placement.ROOTS) -> None:
    """Give an org that has no rows yet a leaf of its own (what the placement worker does)."""
    with create_engine(cluster.url(name, "pipeshub_migrate"), isolation_level="AUTOCOMMIT").connect() as owner, \
            psycopg.connect(jobs_url(cluster, name), autocommit=True) as jobs:
        owner.exec_driver_sql("SET ROLE pipeshub_owner")
        placement.ensure_org_leaf(owner, jobs, org, roots)


A = "a0000000-0000-0000-0000-0000000000{:02x}"


def storage(c, rel: str) -> dict[str, str]:
    return dict(c.execute("SELECT attname, attstorage FROM pg_attribute WHERE attrelid = %s::regclass AND attnum > 0 AND NOT attisdropped", (rel,)))


def leaf_of(c, org: str) -> str:
    return c.execute("SELECT tableoid::regclass::text FROM retrieval.points WHERE org_id = %s LIMIT 1", (org,)).fetchone()[0]


def safe(monkeypatch, on: bool) -> None:
    """Switch HNSW safe mode on or off whatever pgvector version the cluster has (the mode is only a comparison with this constant)."""
    monkeypatch.setattr(placement, "SAFE_BELOW", (99, 0, 0) if on else (0, 0, 0))


def seed_points(cluster, name, org: str, n: int, lexical: bool = False) -> list[int]:
    adopt(cluster, name, org)
    with connect_as(cluster, name, "pipeshub_jobs") as c, uow(c, "pipeshub_jobs", org):
        sks = []
        for i in range(n):
            if lexical:
                sks.append(write_terms(c, org, A.format(i), f"g{i}", {5: 1.0}))
            else:
                put(c, org, A.format(i))
                sks.append(c.execute("SELECT point_sk FROM retrieval.points WHERE org_id = %s AND point_id = %s", (org, A.format(i))).fetchone()[0])
            c.execute("INSERT INTO retrieval.point_text VALUES (%s, %s, 'text', '{}')", (org, sks[-1]))
        return sks


def tombstone(cluster, name, org: str, sks: list[int]) -> None:
    with connect_as(cluster, name, "pipeshub_jobs") as c, uow(c, "pipeshub_jobs", org):
        for sk in sks:
            c.execute("INSERT INTO retrieval.point_tombstones (org_id, point_sk) VALUES (%s, %s)", (org, sk))


def counts(cluster, name, org: str) -> tuple[int, int, int]:
    with cluster.admin(name) as c:
        return tuple(c.execute(f"SELECT count(*) FROM retrieval.{t} WHERE org_id = %s", (org,)).fetchone()[0]
                     for t in ("points", "point_text", "point_tombstones"))


# ---- storage of the point columns ------------------------------------------------------------------------------------------------

def test_the_short_point_columns_and_the_bit_vector_stay_in_the_row_on_every_leaf(rdb, monkeypatch):
    cluster, name = rdb
    placement_env(monkeypatch, cluster, name)
    adopt(cluster, name, ORG_A)
    with cluster.admin(name) as c:
        main = {"org_id", "group_key", "vrid", "block_id", "ent_type", "ent_id", "ent_kind", "ent_level", "bq"}
        for rel in ("retrieval.points", "retrieval.points_h0", "retrieval.points_h7"):
            s = storage(c, rel)
            assert {k: s[k] for k in main} == dict.fromkeys(main, "m"), rel
            assert s["lex_terms"] == "e" and s["embedding"] == "p"          # the vector itself was PLAIN before this wave
    with connect_as(cluster, name, "pipeshub_indexing_login") as c, uow(c, "pipeshub_indexing", ORG_A):
        c.execute("SELECT requested FROM retrieval.org_leaf_request()")
    assert maintain.main(["placement"]) == 0
    with cluster.admin(name) as c:
        assert storage(c, f"retrieval.points_o_{ORG_A}") == storage(c, "retrieval.points")      # a leaf made later copies the settings


# ---- build sizing ----------------------------------------------------------------------------------------------------------------

def test_the_build_gets_the_memory_the_graph_needs_within_the_ceiling():
    p = placement.build_plan(100_000, 1024, (0, 8, 6), ceiling_mb=1024, workers=2)
    assert (p.needed_mb, p.memory_mb, p.workers, p.fits) == (95, 95, 2, True)             # measured: 843 B per point at 1024 bits (82 MB), with 15 % headroom
    assert placement.build_plan(10_000_000, 1024, (0, 8, 6), ceiling_mb=512, workers=2) == placement.BuildPlan(10_000_000, 9_410, 512, 2, False)
    assert placement.build_plan(10, 1024, (0, 8, 6), ceiling_mb=512, workers=2).memory_mb == 64        # never below the server default
    assert placement.build_plan(100_000, 1024, (0, 8, 1), ceiling_mb=1024, workers=4).workers == 0      # serial before the parallel-build fix
    assert placement.build_plan(100_000, 1024, (0, 8, 2), ceiling_mb=1024, workers=4).workers == 4


def test_build_indexes_reports_the_sizing_and_leaves_no_session_setting_behind(rdb, monkeypatch):
    cluster, name = rdb
    placement_env(monkeypatch, cluster, name)
    adopt(cluster, name, ORG_A)
    monkeypatch.setenv("VECTOR_BUILD_WORKERS", "0")
    monkeypatch.setenv("VECTOR_BUILD_MEMORY_MB", "96")
    lines: list[str] = []
    with create_engine(cluster.url(name, "pipeshub_migrate"), isolation_level="AUTOCOMMIT").connect() as conn:
        conn.exec_driver_sql("SET ROLE pipeshub_owner")
        names = [n for leaf in placement.leaves_missing_indexes(conn) for n in placement.build_leaf_indexes(conn, leaf, report=lines.append)]
        assert conn.exec_driver_sql("SHOW max_parallel_maintenance_workers").scalar() == "2"
        assert conn.exec_driver_sql("SHOW maintenance_work_mem").scalar() == "64MB"
    assert len(names) in (0, 8) and (not names or len(lines) == 8)
    assert all("0 worker(s)" in ln and "96 MB" not in ln.split("graph needs")[0] for ln in lines)


# ---- HNSW safe mode: the autovacuum option ------------------------------------------------------------------------------------------

def test_safe_mode_stops_autovacuum_from_cleaning_the_vector_indexes_and_an_existing_setting_is_never_changed(rdb, monkeypatch):
    cluster, name = rdb
    placement_env(monkeypatch, cluster, name)
    adopt(cluster, name, ORG_A)

    def options():
        with cluster.admin(name) as c:
            return {r[0]: r[1] for r in c.execute("SELECT oid::regclass::text, reloptions FROM pg_class WHERE oid IN (SELECT relid FROM pg_partition_tree('retrieval.points') WHERE isleaf)")}
    with cluster.admin(name) as c:
        c.execute("ALTER TABLE retrieval.points_h1 SET (vacuum_index_cleanup = on)")           # an operator's setting
    safe(monkeypatch, True)
    assert maintain.main(["nightly"]) == 0
    got = options()
    assert got["retrieval.points_h1"] == ["vacuum_index_cleanup=on"]                          # not overridden
    assert all(o == ["vacuum_index_cleanup=off"] for k, o in got.items() if k != "retrieval.points_h1") and len(got) == 8
    safe(monkeypatch, False)                                                                    # pgvector upgraded: the setting stays until an operator removes it
    assert maintain.main(["nightly"]) == 0
    assert options() == got
    safe(monkeypatch, True)
    with connect_as(cluster, name, "pipeshub_indexing_login") as c, uow(c, "pipeshub_indexing", ORG_A):
        c.execute("SELECT requested FROM retrieval.org_leaf_request()")
    assert maintain.main(["placement"]) == 0
    with cluster.admin(name) as c:
        assert c.execute(f"SELECT reloptions FROM pg_class WHERE oid = 'retrieval.points_o_{ORG_A}'::regclass").fetchone()[0] == ["vacuum_index_cleanup=off"]


# ---- the safe-mode nightly --------------------------------------------------------------------------------------------------------

def relfilenodes(cluster, name, leaf: str) -> dict[str, int]:
    with cluster.admin(name) as c:
        return dict(c.execute("SELECT i.indexrelid::regclass::text, c.relfilenode::int FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
                              "WHERE i.indrelid = %s::regclass AND i.indisvalid AND c.relam = (SELECT oid FROM pg_am WHERE amname = 'hnsw')", (leaf,)))


def nightly_setup(cluster, name, monkeypatch, org=ORG_A, n=6):
    placement_env(monkeypatch, cluster, name)
    sks = seed_points(cluster, name, org, n, lexical=True)
    assert maintain.main(["build-indexes"]) == 0
    with cluster.admin(name) as c:
        leaf = leaf_of(c, org)
    return sks, leaf


def test_nightly_removes_tombstoned_points_rebuilds_the_index_and_drops_the_tombstones(rdb, monkeypatch):
    cluster, name = rdb
    safe(monkeypatch, True)
    sks, leaf = nightly_setup(cluster, name, monkeypatch)
    other = seed_points(cluster, name, ORG_B, 2)                      # another org in the shared tree is left alone
    tombstone(cluster, name, ORG_A, sks[:2])
    before = relfilenodes(cluster, name, leaf)
    assert before and counts(cluster, name, ORG_A) == (6, 6, 2)
    assert maintain.main(["nightly"]) == 0
    assert counts(cluster, name, ORG_A) == (4, 4, 0)
    after = relfilenodes(cluster, name, leaf)
    assert set(after) == set(before) and all(after[k] != before[k] for k in before)        # rebuilt, same names, valid
    assert counts(cluster, name, ORG_B) == (2, 2, 0) and len(other) == 2
    with cluster.admin(name) as c:
        assert c.execute("SELECT count(*) FROM pg_index i JOIN pg_class t ON t.oid = i.indrelid WHERE t.relnamespace = 'retrieval'::regnamespace AND NOT i.indisvalid").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM retrieval.postings WHERE org_id = %s", (ORG_A,)).fetchone()[0] == 6      # postings are the writer's business
    again = relfilenodes(cluster, name, leaf)
    assert maintain.main(["nightly"]) == 0                                                  # nothing left: nothing is rebuilt
    assert relfilenodes(cluster, name, leaf) == again


def test_a_nightly_killed_after_the_purge_is_finished_by_the_next_one(rdb, monkeypatch):
    cluster, name = rdb
    safe(monkeypatch, True)
    sks, leaf = nightly_setup(cluster, name, monkeypatch)
    tombstone(cluster, name, ORG_A, sks[:3])
    before = relfilenodes(cluster, name, leaf)
    with psycopg.connect(jobs_url(cluster, name), autocommit=True) as jobs:
        assert upkeep.purge_tombstones(jobs) == ({ORG_A: 3}, {})                            # the run dies here: points gone, tombstones left
        assert list(upkeep.tombstoned_leaves(jobs).values()) == [leaf]
    assert counts(cluster, name, ORG_A) == (3, 3, 3)
    assert maintain.main(["nightly"]) == 0
    assert counts(cluster, name, ORG_A) == (3, 3, 0)
    assert all(v != before[k] for k, v in relfilenodes(cluster, name, leaf).items())      # the leaf was still rebuilt


def test_nightly_with_the_mode_off_deletes_but_does_not_rebuild(rdb, monkeypatch):
    cluster, name = rdb
    safe(monkeypatch, False)
    sks, leaf = nightly_setup(cluster, name, monkeypatch)
    tombstone(cluster, name, ORG_A, sks[:2])
    before = relfilenodes(cluster, name, leaf)
    assert maintain.main(["nightly"]) == 0
    assert counts(cluster, name, ORG_A) == (4, 4, 0) and relfilenodes(cluster, name, leaf) == before
    safe(monkeypatch, True)
    assert maintain.main(["nightly", "--all"]) == 0                                       # --all rebuilds every leaf
    assert all(v != before[k] for k, v in relfilenodes(cluster, name, leaf).items())


def test_nightly_skips_a_point_that_a_writer_is_reviving_and_leaves_its_tombstone(rdb, monkeypatch):
    cluster, name = rdb
    safe(monkeypatch, True)
    sks, _ = nightly_setup(cluster, name, monkeypatch)
    tombstone(cluster, name, ORG_A, sks[:2])
    with connect_as(cluster, name, "pipeshub_jobs") as w, uow(w, "pipeshub_jobs", ORG_A):
        w.execute("SELECT 1 FROM retrieval.points WHERE org_id = %s AND point_sk = %s FOR UPDATE", (ORG_A, sks[0]))       # an upsert holds it
        with psycopg.connect(jobs_url(cluster, name), autocommit=True) as jobs:
            assert upkeep.purge_tombstones(jobs) == ({ORG_A: 1}, {})                        # does not wait for it
            assert upkeep.drop_tombstones(jobs) == ({ORG_A: 1}, {})
    assert counts(cluster, name, ORG_A) == (5, 5, 1)                                        # the other one went; this one and its tombstone stay
    assert maintain.main(["nightly"]) == 0
    assert counts(cluster, name, ORG_A) == (4, 4, 0)


def test_nightly_drops_what_an_interrupted_rebuild_left(rdb, monkeypatch):
    cluster, name = rdb
    safe(monkeypatch, True)
    sks, leaf = nightly_setup(cluster, name, monkeypatch)
    ix = next(iter(relfilenodes(cluster, name, leaf)))
    with cluster.admin(name) as c:
        c.execute(f"CREATE INDEX {ix.split('.')[1]}_ccnew ON {leaf} USING hnsw ((bq::bit(4)) ext.bit_hamming_ops) WHERE space_id = 1")
        c.execute(f"UPDATE pg_index SET indisvalid = false WHERE indexrelid = '{ix}_ccnew'::regclass")        # what a failed REINDEX CONCURRENTLY leaves
    tombstone(cluster, name, ORG_A, sks[:1])
    assert maintain.main(["nightly"]) == 0
    with cluster.admin(name) as c:
        assert c.execute("SELECT to_regclass(%s)", (f"{ix}_ccnew",)).fetchone()[0] is None


def test_a_failing_org_does_not_stop_the_others_and_the_run_reports_it(monkeypatch):
    left = {"a": 5, "c": 2}

    def step(_, org):
        if org == "bad":
            raise pg.DeadlockDetected("deadlock detected\nDETAIL: ...")
        n = min(3, left[org])
        left[org] -= n
        return n
    assert fold.each_org(None, ["a", "bad", "c"], step, 3) == ({"a": 5, "c": 2}, {"bad": "deadlock detected"})


def test_two_runs_at_once_do_not_work_the_same_org(rdb, monkeypatch):
    cluster, name = rdb
    placement_env(monkeypatch, cluster, name)
    sks = seed_points(cluster, name, ORG_A, 3)
    tombstone(cluster, name, ORG_A, sks)
    with psycopg.connect(jobs_url(cluster, name), autocommit=True) as one, psycopg.connect(jobs_url(cluster, name), autocommit=True) as two:
        with one.transaction():
            assert fold.org_lock(one, ORG_A, upkeep.PURGE_LOCK)
            assert upkeep.purge_org(two, ORG_A) == fold.BUSY and upkeep.drop_org(two, ORG_A) == fold.BUSY       # busy: nothing done, no error, not an empty batch
            assert fold.org_lock(one, ORG_A, upkeep.GC_LOCK)
            assert upkeep.group_gc_org(two, ORG_A) == fold.BUSY
        assert upkeep.purge_org(two, ORG_A) == 3


# ---- group collection -------------------------------------------------------------------------------------------------------------

def test_group_gc_removes_only_groups_without_points_older_than_the_grace_and_is_repeatable(rdb, monkeypatch):
    cluster, name = rdb
    placement_env(monkeypatch, cluster, name)
    seed_points(cluster, name, ORG_A, 2)                               # points with group keys A.format(0), A.format(1)
    seed_points(cluster, name, ORG_B, 1)
    with cluster.admin(name) as c:
        for org, key, age in ((ORG_A, A.format(0), "2 days"), (ORG_A, "gone-old", "2 days"), (ORG_A, "gone-new", "1 minute"),
                              (ORG_B, "gone-old", "2 days")):
            c.execute("INSERT INTO retrieval.point_groups VALUES (%s, 1, %s, '{c}', '{}', '{}', now() - %s::interval)", (org, key, age))
            c.execute("INSERT INTO retrieval.group_members VALUES (%s, 1, %s, 1, 'c')", (org, key))
        c.execute("INSERT INTO retrieval.point_groups VALUES (%s, 2, 'gone-old', '{c}', '{}', '{}', now() - interval '2 days')", (ORG_A,))   # another collection
    assert maintain.main(["group-gc"]) == 0

    def groups(org):
        with cluster.admin(name) as c:
            return sorted(r[0] + "/" + str(r[1]) for r in c.execute("SELECT group_key, collection_id FROM retrieval.point_groups WHERE org_id = %s", (org,)))
    assert groups(ORG_A) == [A.format(0) + "/1", "gone-new/1"]          # a group with a point stays; so does one inside the grace
    assert groups(ORG_B) == []                                          # collected in its own org
    with cluster.admin(name) as c:
        assert sorted(r[0] for r in c.execute("SELECT group_key FROM retrieval.group_members WHERE org_id = %s", (ORG_A,))) == [A.format(0), "gone-new"]
        assert c.execute("SELECT count(*) FROM retrieval.group_members WHERE org_id = %s", (ORG_B,)).fetchone()[0] == 0
    assert maintain.main(["group-gc"]) == 0 and groups(ORG_A) == [A.format(0) + "/1", "gone-new/1"]    # again: nothing more


def test_group_gc_works_in_batches(rdb, monkeypatch):
    cluster, name = rdb
    placement_env(monkeypatch, cluster, name)
    adopt(cluster, name, ORG_A)
    monkeypatch.setattr(upkeep, "BATCH", 4)
    with cluster.admin(name) as c:
        for i in range(10):
            c.execute("INSERT INTO retrieval.point_groups VALUES (%s, 1, %s, '{}', '{}', '{}', now() - interval '2 days')", (ORG_A, f"g{i}"))
    with psycopg.connect(jobs_url(cluster, name), autocommit=True) as jobs:
        assert upkeep.group_gc(jobs) == ({ORG_A: 10}, {})


# ---- recount racing a fold ----------------------------------------------------------------------------------------------------------

def test_recount_that_loses_to_a_fold_changes_nothing_and_says_busy():
    class Tx:
        def __enter__(self) -> "Tx":
            return self

        def __exit__(self, *a: object) -> bool:
            return False

    class Jobs:
        def transaction(self): return Tx()
        def execute(self, sql, *a):
            if "ISOLATION" in sql:
                return None
            if "advisory" in sql:
                return type("R", (), {"fetchone": lambda s: (True,)})()
            raise pg.SerializationFailure("could not serialize access due to concurrent update")
    assert fold.recount_org(Jobs(), ORG_A) is None


# ---- usage ------------------------------------------------------------------------------------------------------------------

def test_usage_errors_are_refused():
    for argv in (["nightly", "--force"], ["group-gc", "x"], ["fold", "now"], ["recount", "a", "b"], ["build-indexes", "x"]):
        assert maintain.main(argv) == maintain.EXIT_CONFIG


# ---- round 2: review findings ---------------------------------------------------------------------------------------------------------

def alive(cluster, name, sk: int) -> int:
    with cluster.admin(name) as c:
        return c.execute("SELECT count(*) FROM retrieval.points WHERE org_id = %s AND point_sk = %s", (ORG_A, sk)).fetchone()[0]


@pytest.mark.parametrize("update", [True, False], ids=["revived by an update", "revived by a lock only"])
def test_a_point_revived_and_committed_after_the_purge_took_its_snapshot_is_not_deleted(rdb, monkeypatch, update):
    cluster, name = rdb
    safe(monkeypatch, True)
    sks, _ = nightly_setup(cluster, name, monkeypatch)
    tombstone(cluster, name, ORG_A, sks[:2])
    # the purge statement takes its snapshot, then waits; the writer commits its revive in that gap, before the purge locks the rows
    delayed = upkeep.PURGE.replace("WHERE p.org_id = %(org)s ORDER BY", "WHERE p.org_id = %(org)s AND (SELECT pg_sleep(1.5)::text) IS NOT NULL ORDER BY")
    assert delayed != upkeep.PURGE
    monkeypatch.setattr(upkeep, "PURGE", delayed)
    out = {}

    def purge():
        with psycopg.connect(jobs_url(cluster, name), autocommit=True) as jobs:
            out["r"] = upkeep.purge_tombstones(jobs)
    with connect_as(cluster, name, "pipeshub_jobs") as w:
        with uow(w, "pipeshub_jobs", ORG_A):
            w.execute("SELECT 1 FROM retrieval.points WHERE org_id = %s AND point_sk = %s FOR UPDATE", (ORG_A, sks[0]))
            if update:
                w.execute("UPDATE retrieval.points SET vrid = 'revived' WHERE org_id = %s AND point_sk = %s", (ORG_A, sks[0]))
            w.execute("DELETE FROM retrieval.point_tombstones WHERE org_id = %s AND point_sk = %s", (ORG_A, sks[0]))
            t = threading.Thread(target=purge)
            t.start()
            time.sleep(0.5)
        t.join()                                                    # the writer committed while the purge was waiting
    assert out["r"] == ({ORG_A: 1}, {})
    assert alive(cluster, name, sks[0]) == 1 and alive(cluster, name, sks[1]) == 0


def test_a_leaf_whose_rebuild_failed_keeps_its_tombstones_and_the_next_run_rebuilds_it(rdb, monkeypatch):
    cluster, name = rdb
    safe(monkeypatch, True)
    sks, leaf = nightly_setup(cluster, name, monkeypatch)
    other = seed_points(cluster, name, ORG_B, 2)
    tombstone(cluster, name, ORG_A, sks[:2])
    tombstone(cluster, name, ORG_B, other[:1])
    before = relfilenodes(cluster, name, leaf)
    real = upkeep.reindex_leaf

    def fail_for_a(conn, lf, report=None):
        if lf == leaf:
            raise RuntimeError("canceling statement due to lock timeout")
        return real(conn, lf, report)
    monkeypatch.setattr(upkeep, "reindex_leaf", fail_for_a)
    assert maintain.main(["nightly"]) == maintain.EXIT_PARTIAL
    assert counts(cluster, name, ORG_A) == (4, 4, 2)                       # purged, tombstones kept
    assert counts(cluster, name, ORG_B)[2] in (0, 1)
    monkeypatch.setattr(upkeep, "reindex_leaf", real)
    assert maintain.main(["nightly"]) == 0
    assert counts(cluster, name, ORG_A) == (4, 4, 0)
    assert all(v != before[k] for k, v in relfilenodes(cluster, name, leaf).items())


def reloptions(cluster, name, leaf: str):
    with cluster.admin(name) as c:
        return c.execute("SELECT reloptions FROM pg_class WHERE oid = %s::regclass", (leaf,)).fetchone()[0]


def test_a_bad_build_setting_is_a_startup_error_naming_it_and_the_floor_is_the_servers_memory(monkeypatch, capsys):
    for name in ("VECTOR_BUILD_MEMORY_MB", "VECTOR_BUILD_WORKERS"):
        for bad in ("512MB", "-5", "x", " 1 2"):
            monkeypatch.setenv(name, bad)
            with pytest.raises(ValueError, match=name):
                placement.check_build_settings()
            for cmd in ("placement", "build-indexes", "nightly"):           # refused before any connection is made
                assert maintain.main([cmd]) == maintain.EXIT_CONFIG and name in capsys.readouterr().out
        monkeypatch.delenv(name)
    monkeypatch.setenv("VECTOR_BUILD_MEMORY_MB", "0")                           # the ceiling is at least 1 MB, the worker count may be 0
    with pytest.raises(ValueError, match="VECTOR_BUILD_MEMORY_MB"):
        placement.check_build_settings()
    monkeypatch.setenv("VECTOR_BUILD_MEMORY_MB", "96")
    monkeypatch.setenv("VECTOR_BUILD_WORKERS", "0")
    placement.check_build_settings()
    monkeypatch.setenv("VECTOR_BUILD_MEMORY_MB", "")
    monkeypatch.setenv("VECTOR_BUILD_WORKERS", "")                                # empty means unset
    assert placement.build_plan(10, 1024, (0, 8, 6)).workers == placement.DEFAULT_BUILD_WORKERS
    assert placement.build_plan(10, 1024, (0, 8, 6), ceiling_mb=512, workers=2, floor_mb=300).memory_mb == 300     # the server's setting is a floor
    assert placement.build_plan(10_000_000, 1024, (0, 8, 6), ceiling_mb=512, workers=2, floor_mb=300).memory_mb == 512


def wide_point(cluster, name, dim: int, org: str = ORG_A) -> tuple[str, bool, bool]:
    """Register a space of ``dim`` and insert one point with 100-character group_key, vrid and block_id (a record point); returns (outcome, bq is out of
    line, group_key is out of line)."""
    keys = [hashlib.sha256(f"{dim}{i}".encode()).hexdigest()[:50] * 2 for i in range(3)]      # 100 characters that do not compress
    with cluster.admin(name) as c:
        sid = c.execute("INSERT INTO retrieval.embedding_spaces (model, dim, metric) VALUES (%s, %s, 'cosine') RETURNING space_id", (f"wide{dim}", dim)).fetchone()[0]
        c.execute("INSERT INTO retrieval.org_spaces VALUES (%s, %s, %s, 'active')", (org, sid, dim))
        vec = "[" + ",".join(["0.5", "-0.25"] * (dim // 2)) + "]"
        try:
            c.execute("INSERT INTO retrieval.points (org_id, collection_id, space_id, point_id, group_key, vrid, block_id, embedding, embedding_dim) "
                      "VALUES (%s, 1, %s, gen_random_uuid(), %s, %s, %s, %s::ext.halfvec, %s)", (org, sid, *keys, vec, dim))
        except pg.Error as e:
            return str(e).splitlines()[0], False, False
        bq, gk = c.execute("SELECT pg_column_toast_chunk_id(bq) IS NOT NULL, pg_column_toast_chunk_id(group_key) IS NOT NULL "
                           "FROM retrieval.points WHERE embedding_dim = %s", (dim,)).fetchone()
        return "ok", bq, gk


def test_a_3072_dimension_point_stays_in_the_row_and_the_widest_allowed_space_still_stores(rdb):
    cluster, name = rdb
    assert wide_point(cluster, name, 3072) == ("ok", False, False)         # nothing left the row
    assert wide_point(cluster, name, 3840, ORG_B)[0] == "ok"               # the limit stores (the toaster may move columns out of the row)


def test_a_space_wider_than_the_limit_is_refused_when_it_is_adopted_with_a_clear_error(rdb):
    cluster, name = rdb
    with connect_as(cluster, name, "pipeshub_indexing_login") as c, uow(c, "pipeshub_indexing", ORG_A):
        with pytest.raises(pg.InvalidParameterValue, match="p_dim must be 1..3840"):
            c.execute("SELECT * FROM retrieval.org_space_adopt('wide', 3841, 'cosine')")
    with connect_as(cluster, name, "pipeshub_indexing_login") as c, uow(c, "pipeshub_indexing", ORG_A):
        assert c.execute("SELECT * FROM retrieval.org_space_adopt('wide', 3840, 'cosine')").fetchone() is not None
    with cluster.admin(name) as c, pytest.raises(pg.CheckViolation):
        c.execute("INSERT INTO retrieval.embedding_spaces (model, dim, metric) VALUES ('w', 3841, 'cosine')")      # the table says the same


def test_recount_exits_6_when_an_org_was_busy(rdb, monkeypatch):
    cluster, name = rdb
    placement_env(monkeypatch, cluster, name)
    adopt(cluster, name, ORG_A)
    assert maintain.main(["recount", ORG_A]) == 0
    with psycopg.connect(jobs_url(cluster, name), autocommit=True) as one, one.transaction():
        assert fold.org_lock(one, ORG_A)
        assert maintain.main(["recount", ORG_A]) == maintain.EXIT_PARTIAL


def test_a_tombstone_leaf_is_paired_with_its_points_leaf_from_the_catalog_and_a_partly_placed_org_is_left_alone(rdb, monkeypatch):
    cluster, name = rdb
    safe(monkeypatch, True)
    placement_env(monkeypatch, cluster, name)
    adopt(cluster, name, ORG_B)
    place(cluster, name, ORG_B)                                   # org B has a leaf of its own in every table, org A shares the hash leaves
    other = seed_points(cluster, name, ORG_B, 2)
    tombstone(cluster, name, ORG_B, other[:1])
    sks = seed_points(cluster, name, ORG_A, 3)
    tombstone(cluster, name, ORG_A, sks[:2])
    with psycopg.connect(jobs_url(cluster, name), autocommit=True) as jobs:
        pairs = upkeep.tombstoned_leaves(jobs)
        tomb = next(t for t in pairs if "_h" in t)
        assert pairs[tomb] == tomb.replace("point_tombstones", "points")
        assert pairs[f"retrieval.point_tombstones_o_{ORG_B}"] == f"retrieval.points_o_{ORG_B}"
        assert len(pairs) == 2
    # an org with a points leaf of its own while its tombstones sit in a hash leaf: only part of the org is placed
    adopt(cluster, name, ORG_P)
    place(cluster, name, ORG_P, ("retrieval.points",))
    part = seed_points(cluster, name, ORG_P, 3)
    tombstone(cluster, name, ORG_P, part[:2])
    with psycopg.connect(jobs_url(cluster, name), autocommit=True) as jobs:
        pairs = upkeep.tombstoned_leaves(jobs)
        assert pairs[tomb] == tomb.replace("point_tombstones", "points") and pairs[f"retrieval.point_tombstones_o_{ORG_B}"] == f"retrieval.points_o_{ORG_B}"
        assert [t for t, lf in pairs.items() if lf is None] == [t for t in pairs if t not in (tomb, f"retrieval.point_tombstones_o_{ORG_B}")]
        assert len([t for t, lf in pairs.items() if lf is None]) == 1
    assert maintain.main(["nightly"]) == maintain.EXIT_PARTIAL
    assert counts(cluster, name, ORG_P)[2] == 2                    # nothing was dropped for it


def xmins(cluster, name) -> dict[str, str]:
    with cluster.admin(name) as c:
        return dict(c.execute("SELECT oid::regclass::text, xmin::text FROM pg_class WHERE oid IN (SELECT relid FROM pg_partition_tree('retrieval.points') WHERE isleaf)"))


def test_nightly_leaves_a_leaf_that_already_has_a_setting_alone_and_skips_a_locked_one_without_waiting(rdb, monkeypatch):
    cluster, name = rdb
    placement_env(monkeypatch, cluster, name)
    safe(monkeypatch, True)
    with cluster.admin(name) as holder:
        holder.autocommit = False
        holder.execute("LOCK TABLE retrieval.points_h0 IN SHARE UPDATE EXCLUSIVE MODE")
        started = time.monotonic()
        assert maintain.main(["nightly"]) == maintain.EXIT_PARTIAL
        assert time.monotonic() - started < 15                   # one 2 s wait for the busy leaf, not the holder's whole transaction
        holder.rollback()
    assert reloptions(cluster, name, "retrieval.points_h0") is None                              # skipped
    assert all(reloptions(cluster, name, f"retrieval.points_h{i}") == ["vacuum_index_cleanup=off"] for i in range(1, 8))
    before = xmins(cluster, name)
    assert maintain.main(["nightly"]) == 0                       # the next run sets it
    assert reloptions(cluster, name, "retrieval.points_h0") == ["vacuum_index_cleanup=off"]
    after = xmins(cluster, name)
    assert maintain.main(["nightly"]) == 0
    assert xmins(cluster, name) == after and {k: v for k, v in after.items() if k != "retrieval.points_h0"} == {k: v for k, v in before.items() if k != "retrieval.points_h0"}


# ---- point-count fold, vector indexes, bounded passes ----------------------------------------------------------------------------------

def delta(c, org: str, d: int, coll: int = 1) -> None:
    c.execute("INSERT INTO retrieval.lexical_df_delta (org_id, collection_id, space_id, term, d) VALUES (%s, %s, 1, -1, %s)", (org, coll, d))


def n_of(cluster, name, org: str) -> list[int]:
    with cluster.admin(name) as c:
        return [r[0] for r in c.execute("SELECT n_points FROM retrieval.collection_stats WHERE org_id = %s", (org,))]


def test_the_fold_adds_the_count_deltas_of_each_org_once_and_recount_repairs_a_drifted_counter(rdb, monkeypatch):
    cluster, name = rdb
    adopt(cluster, name, ORG_A)
    adopt(cluster, name, ORG_B)
    placement_env(monkeypatch, cluster, name)
    monkeypatch.setattr(fold, "BATCH", 3)
    with connect_as(cluster, name, "pipeshub_indexing_login") as c:
        for d in (2, 1, -1, 3, 1):
            with uow(c, "pipeshub_indexing", ORG_A):
                delta(c, ORG_A, d)
        with uow(c, "pipeshub_indexing", ORG_B):
            delta(c, ORG_B, 7)
        with uow(c, "pipeshub_indexing", ORG_A), pytest.raises(pg.InsufficientPrivilege):
            c.execute("SELECT * FROM retrieval.collection_stats FOR UPDATE")             # a service does not write the counter
    assert maintain.main(["fold"]) == 0
    assert n_of(cluster, name, ORG_A) == [6] and n_of(cluster, name, ORG_B) == [7]        # a backlog of five folded in batches of three
    with cluster.admin(name) as c:
        assert c.execute("SELECT count(*) FROM retrieval.lexical_df_delta").fetchone()[0] == 0
    assert maintain.main(["fold"]) == 0
    assert n_of(cluster, name, ORG_A) == [6]                                              # a second run changes nothing
    # the counter says 6 and 2 points are alive (one tombstoned): a recount makes it 1, deltas not folded yet included
    with connect_as(cluster, name, "pipeshub_indexing_login") as c, uow(c, "pipeshub_indexing", ORG_A):
        sk = [write_terms(c, ORG_A, A.format(i), f"g{i}", {5: 1.0}) for i in range(3)]
        c.execute("INSERT INTO retrieval.point_tombstones (org_id, point_sk) VALUES (%s, %s)", (ORG_A, sk[0]))
        delta(c, ORG_A, 4)
    assert maintain.main(["recount"]) == 0
    with cluster.admin(name) as c:
        assert c.execute("SELECT n_points FROM retrieval.collection_stats WHERE org_id = %s", (ORG_A,)).fetchone()[0] == 2 - 4
        assert c.execute("SELECT count(*) FROM retrieval.lexical_df_delta WHERE org_id = %s", (ORG_A,)).fetchone()[0] == 1       # the delta stays: the search adds it
    assert n_of(cluster, name, ORG_B) == [0]                                               # an org without points counts zero


def test_a_pass_spends_at_most_max_batches_transactions_on_an_org_and_the_next_pass_finishes(monkeypatch):
    monkeypatch.setattr(fold, "MAX_BATCHES", 2)
    left = {"a": 10}

    def step(_, org):
        n = min(left[org], 3)
        left[org] -= n
        return n

    assert fold.each_org(None, ["a"], step, 3) == ({"a": 6}, {})
    assert fold.each_org(None, ["a"], step, 3) == ({"a": 4}, {})          # the rest, and the following pass finds nothing
    assert fold.each_org(None, ["a"], step, 3) == ({}, {})


def test_vector_indexes_are_built_per_leaf_and_space_and_are_used(rdb, monkeypatch):
    cluster, name = rdb
    adopt(cluster, name, ORG_A)
    place(cluster, name, ORG_A)                                   # the leaf is indexed with the space that exists when it is made
    placement_env(monkeypatch, cluster, name)
    assert maintain.main(["build-indexes"]) == 0                  # the shared hash leaves get theirs
    assert maintain.main(["build-indexes"]) == 0                  # idempotent
    with cluster.admin(name) as c:
        assert c.execute("SELECT count(*) FROM pg_index i JOIN pg_class t ON t.oid = i.indrelid JOIN pg_namespace n ON n.oid = t.relnamespace "
                         "WHERE n.nspname = 'retrieval' AND NOT i.indisvalid").fetchone()[0] == 0
        names = {r[0] for r in c.execute("SELECT indexrelid::regclass::text FROM pg_index WHERE indexrelid::regclass::text ~ '_bq_s1$'")}
        if not names:
            pytest.skip("this pgvector/PostgreSQL pair builds no bit index")
        assert f"retrieval.points_o_{ORG_A}_bq_s1" in names and "retrieval.points_h3_bq_s1" in names
        c.execute("SET enable_seqscan = off")
        plan = "\n".join(r[0] for r in c.execute(
            f"EXPLAIN SELECT point_sk FROM retrieval.points_o_{ORG_A} WHERE space_id = 1 ORDER BY bq::bit(4) OPERATOR(ext.<~>) B'1010' LIMIT 3"))
        assert f"points_o_{ORG_A}_bq_s1" in plan, plan                                   # the planner matches the expression index
        assert c.execute("SELECT count(*) FROM kernel.rls_violations").fetchone()[0] == 0


def test_a_space_registered_after_the_leaves_exist_is_indexed_by_build_indexes_and_the_placement_worker_does_not_build(rdb, monkeypatch):
    """The shared hash leaves are made before any embedding space exists: build-indexes (and the nightly) index them once a space is
    registered, one leaf at a time under the migrate lock. The placement worker's pass never builds an index on a live leaf."""
    cluster, name = rdb
    placement_env(monkeypatch, cluster, name)
    with cluster.admin(name) as c:
        c.execute("INSERT INTO retrieval.embedding_spaces (space_id, model, dim, metric) OVERRIDING SYSTEM VALUE VALUES (7, 'bge', 4, 'cosine')")
    engine = create_engine(cluster.url(name, "pipeshub_migrate"), isolation_level="AUTOCOMMIT")

    def missing() -> list[str]:
        with engine.connect() as owner:
            owner.exec_driver_sql("SET ROLE pipeshub_owner")
            return placement.leaves_missing_indexes(owner)

    first = missing()
    if not first:
        pytest.skip("this pgvector/PostgreSQL pair builds no bit index")
    assert len(first) == 8, first
    assert maintain.main(["placement"]) == 0 and missing() == first          # nothing pending: no index is built by the worker
    taken = []
    real = maintain.locked

    def counting(url):
        taken.append(1)
        return real(url)
    monkeypatch.setattr(maintain, "locked", counting)
    assert maintain.main(["build-indexes"]) == 0
    assert missing() == [] and len(taken) == 8                                  # one migrate lock per leaf
    taken.clear()
    assert maintain.main(["build-indexes"]) == 0 and taken == []                # nothing left: no lock, nothing touched


def test_a_second_nightly_at_once_exits_busy_and_does_nothing(rdb, monkeypatch):
    cluster, name = rdb
    safe(monkeypatch, True)
    sks, _ = nightly_setup(cluster, name, monkeypatch)
    tombstone(cluster, name, ORG_A, sks[:2])
    before = counts(cluster, name, ORG_A)
    assert before == (6, 6, 2)
    with psycopg.connect(jobs_url(cluster, name), autocommit=True) as holder:
        assert upkeep.nightly_lock(holder)                                      # a nightly is running
        assert maintain.main(["nightly"]) == maintain.EXIT_PARTIAL
        assert counts(cluster, name, ORG_A) == before                           # nothing was purged or dropped by the second
    assert maintain.main(["nightly"]) == 0                                      # the lock went with the holder's connection
    assert counts(cluster, name, ORG_A) == (4, 4, 0)


class FakeJobs:
    """A connection as a transaction-mode pooler gives it: another backend now and then, or a session lock it does not keep."""

    def __init__(self, pids, held) -> None:
        self.pids, self.held = iter(pids), held

    def execute(self, sql, params=None):
        row = (next(self.pids),) if "pg_backend_pid()" in sql and "count" not in sql else (True,) if "try_advisory" in sql else (self.held,)
        return type("R", (), {"fetchone": lambda s: row})()


def test_the_nightly_lock_is_proved_held_and_a_pooler_is_refused():
    assert upkeep.nightly_lock(FakeJobs([7, 7, 7], 1)) is True
    for jobs in (FakeJobs([7, 8, 7], 1), FakeJobs([7, 7, 7], 0)):              # another backend per statement / a lock the pooler dropped
        with pytest.raises(upkeep.PoolerError, match="pooler"):
            upkeep.nightly_lock(jobs)


def test_a_nightly_behind_a_pooler_is_a_configuration_error_and_does_nothing(monkeypatch, capsys):
    kept = {}
    monkeypatch.setenv("PG_JOBS_URL", "postgresql://x@127.0.0.1:1/x")
    monkeypatch.setenv("PG_MIGRATE_URL", "postgresql+psycopg://x@127.0.0.1:1/x")
    monkeypatch.setattr(maintain.psycopg, "connect", lambda *a, **k: kept.update(k) or type("C", (), {
        "__enter__": lambda s: FakeJobs([1, 2, 3], 0), "__exit__": lambda s, *e: None})())
    assert maintain.main(["nightly"]) == maintain.EXIT_CONFIG and "pooler" in capsys.readouterr().out
    assert kept["keepalives"] == 1 and kept["autocommit"] is True                   # the long connection notices a dead link


def test_group_gc_skips_a_group_a_writer_holds_uncommitted_and_collects_it_after_a_rollback(rdb, monkeypatch):
    """The writer's group statement (INSERT ... ON CONFLICT DO UPDATE) locks the group row even when its fence leaves the row as it is;
    the collection takes its candidates with SKIP LOCKED, so it leaves that group alone."""
    cluster, name = rdb
    placement_env(monkeypatch, cluster, name)
    adopt(cluster, name, ORG_A)
    with connect_as(cluster, name, "pipeshub_jobs") as c, uow(c, "pipeshub_jobs", ORG_A):
        c.execute("INSERT INTO retrieval.point_groups (org_id, collection_id, group_key, computed_at) VALUES (%s, 1, 'g', now() - interval '2 hours')", (ORG_A,))
        c.execute("INSERT INTO retrieval.group_members VALUES (%s, 1, 'g', 1, 'conn1')", (ORG_A,))
    class Rollback(Exception):
        pass

    with pytest.raises(Rollback), connect_as(cluster, name, "pipeshub_jobs") as held, uow(held, "pipeshub_jobs", ORG_A):
        held.execute("INSERT INTO retrieval.point_groups AS pg (org_id, collection_id, group_key, computed_at) VALUES (%s, 1, 'g', now() - interval '1 hour') "
                     "ON CONFLICT (org_id, collection_id, group_key) DO UPDATE SET computed_at = EXCLUDED.computed_at WHERE pg.computed_at < EXCLUDED.computed_at", (ORG_A,))
        monkeypatch.setenv("PG_JOBS_URL", jobs_url(cluster, name) + "?options=-c%20lock_timeout%3D3000")      # a revert fails here instead of hanging
        assert maintain.main(["group-gc"]) == 0                                 # the row is locked by the open transaction
        with cluster.admin(name) as a:
            assert a.execute("SELECT count(*) FROM retrieval.point_groups WHERE group_key = 'g'").fetchone()[0] == 1
            assert a.execute("SELECT count(*) FROM retrieval.group_members WHERE group_key = 'g'").fetchone()[0] == 1
        raise Rollback
    assert maintain.main(["group-gc"]) == 0
    with cluster.admin(name) as a:
        assert a.execute("SELECT count(*) FROM retrieval.point_groups WHERE group_key = 'g'").fetchone()[0] == 0


def test_a_one_shot_fold_exits_6_when_an_org_failed_and_a_failing_org_does_not_hold_up_the_ones_behind_it(monkeypatch):
    seen = []

    def step(_, org):
        seen.append(org)
        if org == "a":
            raise psycopg.errors.DeadlockDetected("deadlock detected")
        return 0

    pages = {"": ["a", "b"], "b": ["c"], "c": []}
    monkeypatch.setattr(fold, "pending_orgs", lambda jobs, limit=1000, after="": pages[after])
    monkeypatch.setattr(fold, "fold_org", step)
    assert fold.run(None) == ({}, {"a": "deadlock detected"}) and seen == ["a", "b", "c"]
    monkeypatch.setattr(fold, "run", lambda jobs: ({}, {"a": "x"}))
    monkeypatch.setattr(maintain.psycopg, "connect", lambda *a, **k: type("C", (), {"__enter__": lambda s: None, "__exit__": lambda s, *e: None})())
    monkeypatch.setenv("PG_JOBS_URL", "postgresql://x@127.0.0.1:1/x")
    assert maintain.main(["fold"]) == maintain.EXIT_PARTIAL


def test_recount_refuses_a_flag_or_a_malformed_org_before_touching_anything(monkeypatch):
    for bad in ("--watch", "x", "A" * 24, "a" * 23):
        assert maintain.main(["recount", bad]) == maintain.EXIT_CONFIG


def test_a_job_that_finds_an_org_busy_reports_it_and_exits_6_and_the_next_run_does_the_work(rdb, monkeypatch):
    cluster, name = rdb
    placement_env(monkeypatch, cluster, name)
    adopt(cluster, name, ORG_A)
    with connect_as(cluster, name, "pipeshub_indexing_login") as c, uow(c, "pipeshub_indexing", ORG_A):
        delta(c, ORG_A, 5)
    with psycopg.connect(jobs_url(cluster, name), autocommit=True) as one, one.transaction():
        assert fold.org_lock(one, ORG_A) and fold.org_lock(one, ORG_A, upkeep.GC_LOCK)
        assert maintain.main(["fold"]) == maintain.EXIT_PARTIAL                        # busy: not an empty batch, not exit 0
        assert maintain.main(["group-gc"]) == maintain.EXIT_PARTIAL
    assert maintain.main(["fold"]) == 0 and n_of(cluster, name, ORG_A) == [5]          # the next run folds it
    assert maintain.main(["group-gc"]) == 0


def test_a_leaf_whose_index_was_left_invalid_by_an_interrupted_build_is_found_and_repaired(rdb, monkeypatch):
    cluster, name = rdb
    placement_env(monkeypatch, cluster, name)
    adopt(cluster, name, ORG_A)
    assert maintain.main(["build-indexes"]) == 0
    index = "retrieval.points_h0_bq_s1"
    with cluster.admin(name) as c:
        if c.execute("SELECT to_regclass(%s)", (index,)).fetchone()[0] is None:
            pytest.skip("this pgvector/PostgreSQL pair builds no bit index")
        c.execute("UPDATE pg_index SET indisvalid = false WHERE indexrelid = %s::regclass", (index,))      # what a killed CREATE INDEX CONCURRENTLY leaves
    engine = create_engine(cluster.url(name, "pipeshub_migrate"), isolation_level="AUTOCOMMIT")
    with engine.connect() as owner:
        owner.exec_driver_sql("SET ROLE pipeshub_owner")
        assert placement.leaves_missing_indexes(owner) == ["retrieval.points_h0"]       # counted as missing, though the name exists
    assert maintain.main(["build-indexes"]) == 0
    with cluster.admin(name) as c:
        assert c.execute("SELECT indisvalid FROM pg_index WHERE indexrelid = %s::regclass", (index,)).fetchone()[0] is True
