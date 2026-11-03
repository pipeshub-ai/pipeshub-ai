"""The placement worker (``python -m db.maintain placement``): an org's own LIST leaf, made before its first row, for every request the
writing service filed; what is refused, retried, repeated and interrupted. Against a migrated database (PostgreSQL 17 and 18)."""

import threading

import psycopg
import pytest
from psycopg import errors as pg

from db import maintain
from db.lib import placement
from db.migrate import RunnerError, locked

from .rls_fixture import ORG_A, ORG_B, uow
from .test_retrieval import BIGGER, VEC, adopt, connect_as, put

ASK = "SELECT requested FROM retrieval.org_leaf_request()"


def env(monkeypatch, cluster, name):
    monkeypatch.setenv("PG_MIGRATE_URL", cluster.url(name, "pipeshub_migrate"))
    monkeypatch.setenv("PG_JOBS_URL", f"postgresql://pipeshub_jobs@127.0.0.1:{cluster.port}/{name}")


def ask(cluster, name, org):
    with connect_as(cluster, name, "pipeshub_indexing_login") as c, uow(c, "pipeshub_indexing", org):
        c.execute(ASK)


def status(cluster, name, org):
    with connect_as(cluster, name, "pipeshub_indexing_login") as c, uow(c, "pipeshub_indexing", org):
        return c.execute("SELECT placed, note FROM retrieval.org_leaf_status()").fetchall()


def attached(cluster, name, org):
    with cluster.admin(name) as c:
        return [t for t in BIGGER if c.execute("SELECT coalesce((SELECT relispartition FROM pg_class WHERE oid = to_regclass(%s)), false)",
                                               (f"retrieval.{t}_o_{org}",)).fetchone()[0]]


def test_an_orgs_leaf_exists_before_its_first_row_and_holds_only_its_rows(rdb, monkeypatch):
    cluster, name = rdb
    for _ in range(2):                                                                      # asking twice is harmless
        ask(cluster, name, ORG_A)
    with connect_as(cluster, name, "pipeshub_indexing_login") as c, uow(c, "pipeshub_indexing", None):
        assert c.execute(ASK).fetchall() == []                                              # no org: no request
    assert status(cluster, name, ORG_A) == [(False, None)]
    with connect_as(cluster, name, "pipeshub_query_login") as c, uow(c, "pipeshub_query", ORG_A), pytest.raises(pg.InsufficientPrivilege):
        c.execute(ASK)                                                                      # a reading service cannot ask
    env(monkeypatch, cluster, name)
    assert maintain.main(["placement"]) == 0
    assert status(cluster, name, ORG_A) == [(True, None)]
    assert attached(cluster, name, ORG_A) == list(BIGGER)
    with cluster.admin(name) as c:
        assert c.execute("SELECT count(*) FROM kernel.rls_violations").fetchone()[0] == 0     # the leaves are secured like every partition
        opts = dict(c.execute("SELECT relid::regclass::text, (SELECT reloptions FROM pg_class WHERE oid = relid) "
                              "FROM pg_partition_tree('retrieval.postings') WHERE isleaf").fetchall())
        assert len(opts) == 9 and all(sorted(o or []) == ["autovacuum_vacuum_insert_scale_factor=0.02", "autovacuum_vacuum_insert_threshold=2000"]
                                      for o in opts.values()), opts
        assert c.execute(f"SELECT relrowsecurity AND relforcerowsecurity FROM pg_class WHERE oid = 'retrieval.points_o_{ORG_A}'::regclass").fetchone()[0]
    adopt(cluster, name, ORG_A)
    adopt(cluster, name, ORG_B)
    with connect_as(cluster, name, "pipeshub_indexing_login") as c:
        with uow(c, "pipeshub_indexing", ORG_A):
            put(c, ORG_A, "a0000000-0000-0000-0000-0000000000f1")
        with uow(c, "pipeshub_indexing", ORG_B):
            put(c, ORG_B, "b0000000-0000-0000-0000-0000000000f1")
    with cluster.admin(name) as c:
        assert c.execute(f"SELECT array_agg(DISTINCT org_id) FROM retrieval.points_o_{ORG_A}").fetchone()[0] == [ORG_A]       # B's row went elsewhere
        assert c.execute("SELECT count(*) FROM retrieval.points_default WHERE org_id = %s", (ORG_B,)).fetchone()[0] == 1
        assert c.execute("SELECT count(*) FROM retrieval.points_default WHERE org_id = %s", (ORG_A,)).fetchone()[0] == 0
        with pytest.raises(pg.CheckViolation):                                              # the leaf itself refuses another org's row
            c.execute(f"INSERT INTO retrieval.points_o_{ORG_A} (org_id, collection_id, space_id, point_id, group_key, embedding, embedding_dim) "
                      "VALUES (%s, 1, 1, gen_random_uuid(), 'g', %s::ext.halfvec, 4)", (ORG_B, VEC))
    with connect_as(cluster, name, "pipeshub_query_login") as c, uow(c, "pipeshub_query", ORG_B):
        assert c.execute("SELECT count(*) FROM retrieval.points").fetchone()[0] == 1          # row-level security still pins the org through the root
    assert maintain.main(["placement"]) == 0                                                # nothing pending
    from .conftest import run_migrate
    assert run_migrate(cluster.url(name, "pipeshub_migrate")).returncode == 0              # and the next deploy is clean


def test_the_points_leaf_gets_the_vector_index_of_each_registered_space_and_the_cleanup_option_of_the_mode(rdb, monkeypatch):
    cluster, name = rdb
    adopt(cluster, name, ORG_A)                                                             # a space exists before the leaf
    ask(cluster, name, ORG_A)
    env(monkeypatch, cluster, name)
    assert maintain.main(["placement"]) == 0
    with cluster.admin(name) as c:
        c.execute("SET ROLE pipeshub_owner")
        engine_owner = None
        major = int(c.execute("SELECT current_setting('server_version_num')::int / 10000").fetchone()[0])
        ver = tuple(int(x) for x in c.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'").fetchone()[0].split("."))
        indexes = [r[0] for r in c.execute("SELECT indexname FROM pg_indexes WHERE tablename = %s AND indexname LIKE '%%_bq_s%%'", (f"points_o_{ORG_A}",))]
        assert indexes == ([] if major >= 18 and ver < (0, 8, 3) else [f"points_o_{ORG_A}_bq_s1"])
        opts = c.execute("SELECT coalesce(reloptions, '{}') FROM pg_class WHERE oid = %s::regclass", (f"retrieval.points_o_{ORG_A}",)).fetchone()[0]
        assert ("vacuum_index_cleanup=off" in opts) == (ver < placement.SAFE_BELOW)
        del engine_owner


def test_a_refused_request_is_closed_with_its_reason_and_nothing_is_made(rdb, monkeypatch):
    cluster, name = rdb
    adopt(cluster, name, ORG_A)
    with connect_as(cluster, name, "pipeshub_indexing_login") as c, uow(c, "pipeshub_indexing", ORG_A):
        put(c, ORG_A, "a0000000-0000-0000-0000-0000000000f1")                               # the org already has a row in the shared tree
    ask(cluster, name, ORG_A)
    env(monkeypatch, cluster, name)
    assert maintain.main(["placement"]) == 0
    ((placed, note),) = status(cluster, name, ORG_A)
    assert placed is False and "retrieval.points" in note and "already has rows" in note and "ABC" not in note
    assert attached(cluster, name, ORG_A) == []
    with cluster.admin(name) as c:
        assert c.execute("SELECT failed_at IS NOT NULL, done_at IS NULL, attempts FROM retrieval.placement_requests").fetchone() == (True, True, 0)
        assert c.execute(f"SELECT count(*) FROM retrieval.points_default WHERE org_id = '{ORG_A}'").fetchone()[0] == 1       # the row is where it was
    assert maintain.main(["placement"]) == 0                                                # a closed request is not looked at again
    assert status(cluster, name, ORG_A)[0][1] == note


def test_rows_in_a_later_table_refuse_after_the_earlier_leaves_were_made_and_a_second_run_changes_nothing(rdb, monkeypatch):
    cluster, name = rdb
    with cluster.admin(name) as c:
        c.execute("INSERT INTO retrieval.point_tombstones (org_id, point_sk) VALUES (%s, 1)", (ORG_A,))
    ask(cluster, name, ORG_A)
    env(monkeypatch, cluster, name)
    assert maintain.main(["placement"]) == 0
    assert "retrieval.point_tombstones" in status(cluster, name, ORG_A)[0][1]
    assert attached(cluster, name, ORG_A) == list(BIGGER[:BIGGER.index("point_tombstones")])        # whole tables only, up to the refusal
    before = attached(cluster, name, ORG_A)
    assert maintain.main(["placement"]) == 0
    assert attached(cluster, name, ORG_A) == before


def test_the_same_request_run_twice_and_by_two_workers_at_once_makes_one_leaf(rdb, monkeypatch):
    cluster, name = rdb
    ask(cluster, name, ORG_A)
    ask(cluster, name, ORG_B)
    env(monkeypatch, cluster, name)
    results: list[int] = []
    workers = [threading.Thread(target=lambda: results.append(maintain.main(["placement"]))) for _ in range(4)]
    for w in workers:
        w.start()
    for w in workers:
        w.join(120)
    assert results == [0, 0, 0, 0]                                                          # they take the migrate lock one after the other
    for org in (ORG_A, ORG_B):
        assert attached(cluster, name, org) == list(BIGGER) and status(cluster, name, org) == [(True, None)]
    with cluster.admin(name) as c:
        assert c.execute("SELECT count(*) FROM pg_class WHERE relkind = 'r' AND relname LIKE 'points\\_o\\_%'").fetchone()[0] == 2
        assert c.execute("SELECT count(*) FROM retrieval.placement_requests WHERE done_at IS NOT NULL").fetchone()[0] == 2


class Dying:
    """The owner connection, and a failure when the postings table is attached: the kill of a worker in the middle of its run."""

    def __init__(self, conn) -> None:
        self._conn = conn

    def exec_driver_sql(self, sql, *args, **kw):
        if "ATTACH PARTITION retrieval.postings_o_" in sql:
            raise KeyboardInterrupt("killed")
        return self._conn.exec_driver_sql(sql, *args, **kw)


def test_a_run_killed_in_the_middle_leaves_whole_tables_only_and_the_next_run_finishes(rdb, monkeypatch):
    cluster, name = rdb
    ask(cluster, name, ORG_A)
    env(monkeypatch, cluster, name)
    real = placement.run_requests
    monkeypatch.setattr(placement, "run_requests", lambda owner, jobs, limit=None: real(Dying(owner), jobs, limit))
    with pytest.raises(KeyboardInterrupt):
        maintain._placement_pass(maintain.migrate_url())
    with cluster.admin(name) as c:
        assert c.execute(f"SELECT to_regclass('retrieval.postings_o_{ORG_A}') IS NULL").fetchone()[0]       # the table of the killed step is gone
        assert c.execute("SELECT count(*) FROM pg_locks l JOIN pg_class c ON c.oid = l.relation WHERE c.relname LIKE '%%_o_%%' AND NOT l.granted").fetchone()[0] == 0
    done = attached(cluster, name, ORG_A)
    assert done == list(BIGGER[:BIGGER.index("postings")])
    assert status(cluster, name, ORG_A) == [(False, None)]                                  # still pending: the kill left it open
    monkeypatch.setattr(placement, "run_requests", real)
    assert maintain.main(["placement"]) == 0
    assert attached(cluster, name, ORG_A) == list(BIGGER) and status(cluster, name, ORG_A) == [(True, None)]
    with cluster.admin(name) as c:
        assert c.execute("SELECT count(*) FROM kernel.rls_violations").fetchone()[0] == 0


def test_an_unattached_empty_leaf_of_an_older_run_is_replaced_and_one_that_holds_rows_is_refused(rdb, monkeypatch):
    cluster, name = rdb
    env(monkeypatch, cluster, name)
    with cluster.admin(name) as c:
        c.execute("SET ROLE pipeshub_owner")
        c.execute(f"CREATE TABLE retrieval.point_text_o_{ORG_A} (LIKE retrieval.point_text INCLUDING ALL)")
    ask(cluster, name, ORG_A)
    assert maintain.main(["placement"]) == 0
    assert status(cluster, name, ORG_A) == [(True, None)] and attached(cluster, name, ORG_A) == list(BIGGER)
    with cluster.admin(name) as c:
        c.execute("SET ROLE pipeshub_owner")
        c.execute(f"CREATE TABLE retrieval.point_text_o_{ORG_B} (LIKE retrieval.point_text INCLUDING ALL)")
        c.execute("SET ROLE NONE")
        c.execute(f"INSERT INTO retrieval.point_text_o_{ORG_B} (org_id, point_sk, payload) VALUES (%s, 1, '{{}}')", (ORG_B,))
    ask(cluster, name, ORG_B)
    assert maintain.main(["placement"]) == 0
    assert "exists, is not attached and holds rows" in status(cluster, name, ORG_B)[0][1]
    with cluster.admin(name) as c:
        assert c.execute(f"SELECT count(*) FROM retrieval.point_text_o_{ORG_B}").fetchone()[0] == 1               # nothing was dropped


def test_a_request_that_keeps_failing_backs_off_and_is_given_up(rdb, monkeypatch):
    cluster, name = rdb
    ask(cluster, name, ORG_A)
    env(monkeypatch, cluster, name)

    def broken(*a, **k):
        raise RuntimeError("a lock that would not clear")

    monkeypatch.setattr(placement, "ensure_org_leaf", broken)
    assert maintain.main(["placement"]) == 0
    assert maintain.main(["placement"]) == 0                                                # waiting out its delay: not tried again
    with cluster.admin(name) as c:
        assert c.execute("SELECT attempts, failed_at IS NULL, next_try_at > now() FROM retrieval.placement_requests").fetchone() == (1, True, True)
        for _ in range(placement.MAX_ATTEMPTS - 1):
            c.execute("UPDATE retrieval.placement_requests SET next_try_at = NULL")
            assert maintain.main(["placement"]) == 0
        attempts, failed, note = c.execute("SELECT attempts, failed_at IS NOT NULL, note FROM retrieval.placement_requests").fetchone()
    assert (attempts, failed) == (placement.MAX_ATTEMPTS, True) and "gave up" in note and "would not clear" in note
    assert status(cluster, name, ORG_A)[0][1] == note


def test_requests_that_cannot_be_placed_do_not_starve_new_ones_and_a_new_ask_reopens_an_old_one(rdb, monkeypatch):
    cluster, name = rdb
    monkeypatch.setattr(placement, "MAX_PER_PASS", 3)
    n = 7
    with cluster.admin(name) as c:
        c.execute("INSERT INTO retrieval.embedding_spaces (space_id, model, dim, metric) OVERRIDING SYSTEM VALUE VALUES (1, 'bge', 4, 'cosine')")
        c.execute("INSERT INTO retrieval.org_spaces (org_id, space_id, dim, role) SELECT lpad(to_hex(g), 24, '0'), 1, 4, 'active' FROM generate_series(1, %s) g", (n,))
        c.execute("INSERT INTO retrieval.points (org_id, collection_id, space_id, point_id, group_key, embedding, embedding_dim) "
                  "SELECT lpad(to_hex(g), 24, '0'), 1, 1, gen_random_uuid(), 'g', %s::ext.halfvec, 4 FROM generate_series(1, %s) g", (VEC, n))
        c.execute("INSERT INTO retrieval.placement_requests (org_id, requested_at) SELECT lpad(to_hex(g), 24, '0'), now() - interval '1 day' FROM generate_series(1, %s) g", (n,))
    ask(cluster, name, ORG_A)                                                               # a brand-new org, behind seven that will be refused
    env(monkeypatch, cluster, name)
    pending = "SELECT count(*) FROM retrieval.placement_requests WHERE done_at IS NULL AND failed_at IS NULL"
    assert maintain.main(["placement"]) == 0
    with cluster.admin(name) as c:
        assert c.execute(pending).fetchone()[0] == n + 1 - 3                                 # a pass is capped: it holds the migrate lock
    passes = 1
    while passes < 10:
        with cluster.admin(name) as c:
            if c.execute(pending).fetchone()[0] == 0:
                break
        assert maintain.main(["placement"]) == 0
        passes += 1
    assert passes == 3                                                                      # ceil(8 / 3) passes; closed requests never wait again
    assert status(cluster, name, ORG_A) == [(True, None)]
    with cluster.admin(name) as c:
        assert c.execute("SELECT count(*) FROM retrieval.placement_requests WHERE failed_at IS NOT NULL AND note LIKE '%%already has rows%%'").fetchone()[0] == n
    refused = "0" * 23 + "1"
    with connect_as(cluster, name, "pipeshub_indexing_login") as c, uow(c, "pipeshub_indexing", refused):
        c.execute(ASK)                                                                      # too soon after it was closed: not re-opened
    with cluster.admin(name) as c:
        assert c.execute("SELECT failed_at IS NOT NULL FROM retrieval.placement_requests WHERE org_id = %s", (refused,)).fetchone()[0]
        c.execute("UPDATE retrieval.placement_requests SET failed_at = now() - interval '1 minute' WHERE org_id = %s", (refused,))
    ask(cluster, name, refused)
    with cluster.admin(name) as c:
        assert c.execute("SELECT failed_at, note FROM retrieval.placement_requests WHERE org_id = %s", (refused,)).fetchone() == (None, None)


def test_the_worker_starts_the_next_pass_at_once_after_a_full_one(monkeypatch):
    passes = iter([(["a"] * placement.MAX_PER_PASS, []), ([], ["b"])])
    monkeypatch.setattr(maintain, "_placement_pass", lambda url: next(passes))

    def sleep(_):
        raise KeyboardInterrupt

    monkeypatch.setattr(maintain.time, "sleep", sleep)
    with pytest.raises(KeyboardInterrupt):
        maintain._watch("unused", 1.0)
    assert next(passes, None) is None                                                       # both passes ran before the first sleep


def test_an_idle_worker_never_takes_the_migrate_lock_and_a_busy_one_waits_for_it(rdb, monkeypatch):
    cluster, name = rdb
    env(monkeypatch, cluster, name)
    monkeypatch.setenv("PG_MIGRATE_LOCK_TIMEOUT_S", "1")
    with locked(cluster.url(name, "pipeshub_migrate")):                                     # a deployment holds the lock
        assert maintain._placement_pass(maintain.migrate_url()) == ([], [])                 # nothing to do: no wait, no lock
        ask(cluster, name, ORG_A)
        assert maintain.main(["placement"]) == 3                                            # work to do: it waits for the lock and says so
    assert maintain.main(["placement"]) == 0
    assert status(cluster, name, ORG_A) == [(True, None)]


def test_wrong_arguments_and_a_missing_jobs_url_are_configuration_errors(rdb, monkeypatch):
    cluster, name = rdb
    monkeypatch.setenv("PG_MIGRATE_URL", cluster.url(name, "pipeshub_migrate"))
    monkeypatch.delenv("PG_JOBS_URL", raising=False)
    for argv in ([], ["fold"], ["placement", "--now"], ["placement", "--watch", "soon"], ["placement", "--watch", "0"], ["placement", "--watch", "1", "x"]):
        assert maintain.main(argv) == 2, argv
    assert maintain.main(["placement"]) == 2                                                # no PG_JOBS_URL
    with pytest.raises(RunnerError):
        maintain._jobs_url()
    with pytest.raises(ValueError):
        placement.leaf_name("retrieval.points", "not-an-org")
    with pytest.raises(ValueError):
        placement.leaf_name("retrieval.points; DROP TABLE x", ORG_A)


def test_the_jobs_connection_is_needed_to_see_the_requests(rdb, monkeypatch):
    cluster, name = rdb
    ask(cluster, name, ORG_A)
    with psycopg.connect(f"postgresql://pipeshub_jobs@127.0.0.1:{cluster.port}/{name}", autocommit=True) as jobs:
        assert placement.has_pending(jobs)
    with cluster.admin(name) as c:
        c.execute("SET ROLE pipeshub_owner")
        assert c.execute("SELECT count(*) FROM retrieval.placement_requests").fetchone()[0] == 0     # row-level security hides them from the owner
