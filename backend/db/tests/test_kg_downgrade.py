"""Taking the kg writers away (a development downgrade to kg_0001) leaves a database the catalog check accepts, and an upgrade brings them back."""
import pytest

from .conftest import run_migrate
from .test_integration import connect


def test_downgrading_the_writers_leaves_a_clean_catalog_and_an_upgrade_brings_them_back(db):
    cluster, name = db
    url = cluster.url(name, "pipeshub_migrate")
    r = run_migrate(url)
    assert r.returncode == 0, r.stdout
    r = run_migrate(url, "downgrade", "kg_0001", PG_MIGRATE_ALLOW_DOWNGRADE="1")
    assert r.returncode == 0, r.stdout
    with connect(cluster, name) as c:
        assert [tuple(x) for x in c.execute("SELECT rule, object FROM kernel.rls_violations")] == []
        assert c.execute("SELECT count(*) FROM pg_proc WHERE pronamespace = 'kg'::regnamespace").fetchone()[0] == 0
    r = run_migrate(url)
    assert r.returncode == 0, r.stdout
    with connect(cluster, name) as c:
        assert c.execute("SELECT count(DISTINCT proc) FROM kernel.vetted_write_pins WHERE rel::text LIKE 'kg.%'").fetchone()[0] == 29


def test_downgrading_the_writers_touches_nothing_that_is_not_theirs(db):
    """A function of kg that another revision owns, with its registry row and its pin on a table the writers also write, survives the downgrade, and so does the write grant that pin needs."""
    cluster, name = db
    url = cluster.url(name, "pipeshub_migrate")
    r = run_migrate(url)
    assert r.returncode == 0, r.stdout
    with connect(cluster, name) as c:
        c.execute("CREATE FUNCTION kg.sentinel_not_mine() RETURNS int LANGUAGE sql AS 'SELECT 1'")
        c.execute("INSERT INTO kernel.function_registry (proc, class) VALUES ('kg.sentinel_not_mine()'::regprocedure, 'read')")
        c.execute("INSERT INTO kernel.vetted_write_pins (proc, rel, op, key_cols, set_cols, txn_col, max_items) VALUES ('kg.sentinel_not_mine()'::regprocedure, 'kg.node_keys'::regclass, 'upsert', NULL, '{}', NULL, 1)")
        c.execute("INSERT INTO kernel.vetted_write_pins (proc, rel, op, key_cols, set_cols, txn_col, max_items) VALUES ('kg.sentinel_not_mine()'::regprocedure, 'kg.links'::regclass, 'insert', NULL, NULL, NULL, 1)")
        others = lambda cur: sorted(  # noqa: E731
            [tuple(x) for x in cur.execute("SELECT proc::text FROM kernel.function_registry WHERE proc::text NOT LIKE 'kg.w\\_%' ORDER BY 1")],
        )
        before = others(c)
        pins_before = sorted(tuple(x) for x in c.execute("SELECT proc::text, rel::text, op FROM kernel.vetted_write_pins WHERE proc::text NOT LIKE 'kg.w\\_%'"))
    r = run_migrate(url, "downgrade", "kg_0001", PG_MIGRATE_ALLOW_DOWNGRADE="1")
    assert r.returncode == 0, r.stdout
    with connect(cluster, name) as c:
        assert c.execute("SELECT count(*) FROM pg_proc WHERE pronamespace = 'kg'::regnamespace AND proname LIKE 'w\\_%'").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM pg_proc WHERE pronamespace = 'kg'::regnamespace AND proname = 'sentinel_not_mine'").fetchone()[0] == 1
        assert others(c) == before
        assert sorted(tuple(x) for x in c.execute("SELECT proc::text, rel::text, op FROM kernel.vetted_write_pins WHERE proc::text NOT LIKE 'kg.w\\_%'")) == pins_before
        assert c.execute("SELECT has_table_privilege('pipeshub_vetted_kg', 'kg.node_keys', 'INSERT')").fetchone()[0] is True      # the sentinel's pin still needs it
        assert c.execute("SELECT has_table_privilege('pipeshub_vetted_kg', 'kg.records', 'INSERT')").fetchone()[0] is False       # only the writers' pins gave this one
        # a remaining pin keeps exactly the operations it needs: kg.links had insert and delete pins of the writers, the sentinel keeps insert
        assert [c.execute(f"SELECT has_table_privilege('pipeshub_vetted_kg', 'kg.links', '{op}')").fetchone()[0] for op in ("INSERT", "UPDATE", "DELETE")] == [True, False, False]
        c.execute("DELETE FROM kernel.vetted_write_pins WHERE proc = 'kg.sentinel_not_mine()'::regprocedure")
        c.execute("DELETE FROM kernel.function_registry WHERE proc = 'kg.sentinel_not_mine()'::regprocedure")
        c.execute("DROP FUNCTION kg.sentinel_not_mine()")
        c.execute("SELECT kernel._sync_vetted_privs('kg.node_keys'::regclass)")
        c.execute("SELECT kernel._sync_vetted_privs('kg.links'::regclass)")
        assert [tuple(x) for x in c.execute("SELECT rule, object FROM kernel.rls_violations")] == []
        assert c.execute("SELECT count(*) FROM kernel.pin_violations").fetchone()[0] == 0
    r = run_migrate(url)
    assert r.returncode == 0, r.stdout


def test_downgrading_the_stamp_writer_alone_leaves_the_other_writers_and_a_clean_catalog(db):
    cluster, name = db
    url = cluster.url(name, "pipeshub_migrate")
    r = run_migrate(url)
    assert r.returncode == 0, r.stdout
    r = run_migrate(url, "downgrade", "kg_0004", PG_MIGRATE_ALLOW_DOWNGRADE="1")
    assert r.returncode == 0, r.stdout
    with connect(cluster, name) as c:
        assert c.execute("SELECT count(*) FROM pg_proc WHERE pronamespace = 'kg'::regnamespace AND proname = 'w_stamps_set'").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM pg_proc WHERE pronamespace = 'kg'::regnamespace AND proname LIKE 'w\\_%'").fetchone()[0] == 16
        assert [tuple(x) for x in c.execute("SELECT rule, object FROM kernel.rls_violations")] == []
        assert c.execute("SELECT count(*) FROM kernel.pin_violations").fetchone()[0] == 0
        assert c.execute("SELECT has_table_privilege('pipeshub_vetted_kg', 'kg.records', 'INSERT')").fetchone()[0] is True      # the records writer's pin still needs it
    r = run_migrate(url)
    assert r.returncode == 0, r.stdout
    with connect(cluster, name) as c:
        assert c.execute("SELECT count(*) FROM pg_proc WHERE pronamespace = 'kg'::regnamespace AND proname = 'w_stamps_set'").fetchone()[0] == 1


def test_downgrading_the_restamp_queue_removes_only_its_own_objects(db):
    cluster, name = db
    url = cluster.url(name, "pipeshub_migrate")
    r = run_migrate(url)
    assert r.returncode == 0, r.stdout
    r = run_migrate(url, "downgrade", "kg_0005", PG_MIGRATE_ALLOW_DOWNGRADE="1")
    assert r.returncode == 0, r.stdout
    with connect(cluster, name) as c:
        assert c.execute("SELECT to_regclass('kg.restamp_queue') IS NULL").fetchone()[0] is True
        assert c.execute("SELECT to_regclass('kg.job_id_seq') IS NULL").fetchone()[0] is True
        assert c.execute("SELECT count(*) FROM information_schema.columns WHERE table_schema = 'kg' AND table_name = 'records' AND column_name = 'fence_job'").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM pg_proc WHERE pronamespace = 'kg'::regnamespace AND proname IN ('w_fence_rows', 'w_queue_add', 'w_queue_touch', 'w_queue_release', 'w_queue_done')").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM pg_proc WHERE pronamespace = 'kg'::regnamespace AND proname LIKE 'w\\_%'").fetchone()[0] == 17        # the stamp writer and the rest stay
        assert [tuple(x) for x in c.execute("SELECT rule, object FROM kernel.rls_violations")] == []
        assert c.execute("SELECT count(*) FROM kernel.pin_violations").fetchone()[0] == 0
        assert c.execute("SELECT has_table_privilege('pipeshub_vetted_kg', 'kg.records', 'UPDATE')").fetchone()[0] is True       # the records writers' pins still need it
    r = run_migrate(url)
    assert r.returncode == 0, r.stdout
    with connect(cluster, name) as c:
        assert c.execute("SELECT to_regclass('kg.restamp_queue') IS NOT NULL").fetchone()[0] is True




def test_downgrading_the_access_lists_removes_only_their_own_objects(db):
    cluster, name = db
    url = cluster.url(name, "pipeshub_migrate")
    r = run_migrate(url)
    assert r.returncode == 0, r.stdout
    r = run_migrate(url, "downgrade", "kg_0007", PG_MIGRATE_ALLOW_DOWNGRADE="1")
    assert r.returncode == 0, r.stdout
    with connect(cluster, name) as c:
        assert c.execute("SELECT to_regclass('kg.scopes') IS NULL").fetchone()[0] is True
        assert c.execute("SELECT count(*) FROM information_schema.columns WHERE table_schema = 'kg' AND table_name = 'records' AND column_name = 'r13'").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM pg_proc WHERE pronamespace = 'kg'::regnamespace AND proname IN ('w_scopes_set', 'w_scopes_delete', 'w_r13_set')").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM pg_proc WHERE pronamespace = 'kg'::regnamespace AND proname LIKE 'w\\_%'").fetchone()[0] == 22      # the rest stay
        assert [tuple(x) for x in c.execute("SELECT rule, object FROM kernel.rls_violations")] == []
        assert c.execute("SELECT count(*) FROM kernel.pin_violations").fetchone()[0] == 0
    r = run_migrate(url)
    assert r.returncode == 0, r.stdout
    with connect(cluster, name) as c:
        assert c.execute("SELECT to_regclass('kg.scopes') IS NOT NULL").fetchone()[0] is True


def test_downgrading_the_listing_writer_removes_only_its_own_objects(db):
    cluster, name = db
    url = cluster.url(name, "pipeshub_migrate")
    r = run_migrate(url)
    assert r.returncode == 0, r.stdout
    r = run_migrate(url, "downgrade", "kg_0008", PG_MIGRATE_ALLOW_DOWNGRADE="1")
    assert r.returncode == 0, r.stdout
    with connect(cluster, name) as c:
        assert c.execute("SELECT count(*) FROM pg_proc WHERE pronamespace = 'kg'::regnamespace AND proname = 'w_listing_set'").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM pg_proc WHERE pronamespace = 'kg'::regnamespace AND proname LIKE 'w\\_%'").fetchone()[0] == 25      # the rest stay
        assert c.execute("SELECT count(*) FROM information_schema.columns WHERE table_schema = 'kg' AND table_name = 'records' AND column_name IN ('folder', 'hide_children', 'alien')").fetchone()[0] == 3
        assert c.execute("SELECT count(*) FROM kernel.pin_violations").fetchone()[0] == 0
    r = run_migrate(url)
    assert r.returncode == 0, r.stdout
    with connect(cluster, name) as c:
        assert c.execute("SELECT count(*) FROM pg_proc WHERE pronamespace = 'kg'::regnamespace AND proname = 'w_listing_set'").fetchone()[0] == 1


@pytest.mark.parametrize("down, writer, old_check", [("kg_0009", "w_records_upsert", "%4000%"), ("kg_0011", "w_grants_set", "%p_roles has an item of the wrong length%"),
                                                     ("kg_0016", "w_records_upsert", "%'RESTRICTED')%")],
                         ids=["kg_0010-long-names", "kg_0012-long-roles", "kg_0017-parent-and-grant"])
def test_downgrading_a_revision_that_relaxes_a_writer_keeps_the_writer_and_an_upgrade_replaces_it(db, down, writer, old_check):
    """kg_0010, kg_0012 and kg_0017 replace the body of a writer kg_0002 owns: a downgrade keeps the writer, its registry row and its pin (kg_0002 runs the same generated file),
    and an upgrade from there replaces it again with nothing left over."""
    cluster, name = db
    url = cluster.url(name, "pipeshub_migrate")
    sig = f"'kg.{writer}'::regproc::regprocedure"

    def state(c) -> tuple:
        return (c.execute("SELECT count(*) FROM pg_proc WHERE pronamespace = 'kg'::regnamespace AND proname = %s", (writer,)).fetchone()[0],
                c.execute(f"SELECT count(*) FROM kernel.function_registry WHERE proc = {sig}").fetchone()[0],
                c.execute(f"SELECT count(*) FROM kernel.vetted_write_pins WHERE proc = {sig}").fetchone()[0],
                c.execute("SELECT count(*) FROM pg_proc WHERE pronamespace = 'kg'::regnamespace AND proname = %s AND prosrc LIKE %s", (writer, old_check)).fetchone()[0])
    r = run_migrate(url)
    assert r.returncode == 0, r.stdout
    r = run_migrate(url, "downgrade", down, PG_MIGRATE_ALLOW_DOWNGRADE="1")
    assert r.returncode == 0, r.stdout
    with connect(cluster, name) as c:
        assert state(c) == (1, 1, 1, 0)
        assert [tuple(x) for x in c.execute("SELECT rule, object FROM kernel.rls_violations")] == []
        assert c.execute("SELECT count(*) FROM kernel.pin_violations").fetchone()[0] == 0
    r = run_migrate(url)
    assert r.returncode == 0, r.stdout
    with connect(cluster, name) as c:
        assert state(c) == (1, 1, 1, 0)
        assert c.execute("SELECT count(*) FROM kernel.pin_violations").fetchone()[0] == 0
        assert c.execute("SELECT count(DISTINCT proc) FROM kernel.vetted_write_pins WHERE rel::text LIKE 'kg.%'").fetchone()[0] == 29


def test_a_function_that_only_shares_a_writers_name_is_not_taken_away_with_the_writers(db):
    """The writers are selected by exact signature: an overload of a writer's name keeps its function, its registry row and its pin."""
    cluster, name = db
    url = cluster.url(name, "pipeshub_migrate")
    r = run_migrate(url)
    assert r.returncode == 0, r.stdout
    with connect(cluster, name) as c:
        c.execute("CREATE FUNCTION kg.w_records_upsert(text) RETURNS int LANGUAGE sql AS 'SELECT 1'")
        c.execute("INSERT INTO kernel.function_registry (proc, class) VALUES ('kg.w_records_upsert(text)'::regprocedure, 'read')")
        c.execute("INSERT INTO kernel.vetted_write_pins (proc, rel, op, key_cols, set_cols, txn_col, max_items) VALUES ('kg.w_records_upsert(text)'::regprocedure, 'kg.links'::regclass, 'insert', NULL, NULL, NULL, 1)")
    r = run_migrate(url, "downgrade", "kg_0001", PG_MIGRATE_ALLOW_DOWNGRADE="1")
    assert r.returncode == 0, r.stdout
    with connect(cluster, name) as c:
        assert c.execute("SELECT count(*) FROM pg_proc WHERE pronamespace = 'kg'::regnamespace AND proname LIKE 'w\\_%'").fetchone()[0] == 1
        assert c.execute("SELECT count(*) FROM pg_proc WHERE oid = 'kg.w_records_upsert(text)'::regprocedure").fetchone()[0] == 1
        assert c.execute("SELECT count(*) FROM kernel.function_registry WHERE proc = 'kg.w_records_upsert(text)'::regprocedure").fetchone()[0] == 1
        assert c.execute("SELECT count(*) FROM kernel.vetted_write_pins WHERE proc = 'kg.w_records_upsert(text)'::regprocedure").fetchone()[0] == 1
        c.execute("DELETE FROM kernel.vetted_write_pins WHERE proc = 'kg.w_records_upsert(text)'::regprocedure")
        c.execute("DELETE FROM kernel.function_registry WHERE proc = 'kg.w_records_upsert(text)'::regprocedure")
        c.execute("DROP FUNCTION kg.w_records_upsert(text)")
        c.execute("SELECT kernel._sync_vetted_privs('kg.links'::regclass)")
    r = run_migrate(url)
    assert r.returncode == 0, r.stdout


def _drop_one_writer_by_hand(c) -> str:
    from pathlib import Path

    from db.lib.vetted import load
    sig = next(f.signature for f in load(Path(__file__).resolve().parents[1] / "versions" / "kg" / "vetted" / "manifest.yaml") if f.qualified == "kg.w_links_delete")
    c.execute(f"DROP FUNCTION {sig}")
    return sig


def test_a_downgrade_refuses_when_a_dropped_writer_left_its_rows_behind(db):
    cluster, name = db
    url = cluster.url(name, "pipeshub_migrate")
    r = run_migrate(url)
    assert r.returncode == 0, r.stdout
    with connect(cluster, name) as c:
        _drop_one_writer_by_hand(c)
    r = run_migrate(url, "downgrade", "kg_0002", PG_MIGRATE_ALLOW_DOWNGRADE="1")      # the later revisions take their own writers back first, each in its own step
    assert r.returncode == 0, r.stdout
    with connect(cluster, name) as c:
        pins = c.execute("SELECT count(*) FROM kernel.vetted_write_pins").fetchone()[0]
    r = run_migrate(url, "downgrade", "kg_0001", PG_MIGRATE_ALLOW_DOWNGRADE="1")
    assert r.returncode != 0 and "no longer exists" in r.stdout, r.stdout
    with connect(cluster, name) as c:        # nothing was deleted or dropped
        assert c.execute("SELECT count(*) FROM kernel.vetted_write_pins").fetchone()[0] == pins
        assert c.execute("SELECT count(*) FROM pg_proc WHERE pronamespace = 'kg'::regnamespace AND proname LIKE 'w\\_%'").fetchone()[0] == 15


def test_a_downgrade_goes_through_when_a_dropped_writer_took_its_rows_with_it(db):
    cluster, name = db
    url = cluster.url(name, "pipeshub_migrate")
    r = run_migrate(url)
    assert r.returncode == 0, r.stdout
    with connect(cluster, name) as c:
        sig = _drop_one_writer_by_hand(c)
        c.execute("DELETE FROM kernel.vetted_write_pins WHERE proc::oid NOT IN (SELECT oid FROM pg_proc)")
        c.execute("DELETE FROM kernel.function_registry WHERE proc::oid NOT IN (SELECT oid FROM pg_proc)")
        assert sig
        c.execute("SELECT kernel._sync_vetted_privs('kg.links'::regclass)")
    r = run_migrate(url, "downgrade", "kg_0001", PG_MIGRATE_ALLOW_DOWNGRADE="1")
    assert r.returncode == 0, r.stdout
    with connect(cluster, name) as c:
        assert [tuple(x) for x in c.execute("SELECT rule, object FROM kernel.rls_violations")] == []
        assert c.execute("SELECT count(*) FROM pg_proc WHERE pronamespace = 'kg'::regnamespace AND proname LIKE 'w\\_%'").fetchone()[0] == 0
    r = run_migrate(url)
    assert r.returncode == 0, r.stdout


def test_downgrading_the_projector_epoch_gives_the_writers_back_their_earlier_signatures(db):
    """kg_0013 down: the six writers lose the projection code epoch again (the signatures of kg_0012, which the code of that release calls), the catalog stays
    clean, and an upgrade takes it back with nothing left over."""
    cluster, name = db
    url = cluster.url(name, "pipeshub_migrate")
    old = ["kg.w_stamps_set(bigint[],smallint[],text[],smallint[],bytea[],bigint[],boolean[],boolean[],boolean[],boolean[],smallint[],boolean[])",
           "kg.w_fence_rows(bigint[],smallint[],text[],smallint[],bigint[])", "kg.w_queue_touch(bigint,bigint,text,integer,text)", "kg.w_queue_done(bigint)",
           "kg.w_changes_ack()", "kg.w_changes_delete(bigint[])"]
    new = [s[:-1] + (",integer)" if not s.endswith("()") else "integer)") for s in old]

    def present(c, sigs: list[str]) -> list[bool]:
        return [c.execute("SELECT to_regprocedure(%s) IS NOT NULL", (s,)).fetchone()[0] for s in sigs]

    def clean_catalog(c, pinned: int) -> None:
        assert [tuple(x) for x in c.execute("SELECT rule, object FROM kernel.rls_violations")] == []
        assert c.execute("SELECT count(*) FROM kernel.pin_violations").fetchone()[0] == 0
        assert c.execute("SELECT count(DISTINCT proc) FROM kernel.vetted_write_pins WHERE rel::text LIKE 'kg.%'").fetchone()[0] == pinned

    r = run_migrate(url)
    assert r.returncode == 0, r.stdout
    with connect(cluster, name) as c:
        assert present(c, old) == [False] * 6 and present(c, new) == [True] * 6
    r = run_migrate(url, "downgrade", "kg_0012", PG_MIGRATE_ALLOW_DOWNGRADE="1")
    assert r.returncode == 0, r.stdout
    with connect(cluster, name) as c:
        assert present(c, old) == [True] * 6 and present(c, new) == [False] * 6
        clean_catalog(c, 26)                                                # the counters' two writers (kg_0015) and the analyze request's (kg_0016) went down too
    r = run_migrate(url)
    assert r.returncode == 0, r.stdout
    with connect(cluster, name) as c:
        assert present(c, old) == [False] * 6 and present(c, new) == [True] * 6
        clean_catalog(c, 29)


def test_the_parent_child_links_revision_moves_the_floor_and_the_kinds_and_its_downgrade_takes_them_back(db):
    """kg_0014: the links writers take kind 3 (a PARENT_CHILD parent) and the projection writers refuse epoch 5 (code that wrote no such links); down to kg_0013, the
    writers take epoch 5 again and the links writers keep kind 3 (older code reads no such kind and drops the rows as it copies each node again), the catalog is clean;
    an upgrade takes the floor back."""
    import psycopg

    from .rls_fixture import ORG_A as org
    from .test_kg_writers import EPOCH, call
    cluster, name = db
    url = cluster.url(name, "pipeshub_migrate")

    def accepts(c, fn: str, args: tuple, epoch: int | None = None) -> bool:
        try:
            if epoch is None:
                call(c, fn, org, args)
            else:
                call(c, fn, org, args, epoch=epoch)
        except psycopg.errors.InvalidParameterValue:
            return False
        return True

    def state(c) -> tuple[bool, bool, bool]:
        return (accepts(c, "kg.w_links_set", ([1], [3], [2])), accepts(c, "kg.w_changes_ack", (), 5), accepts(c, "kg.w_changes_ack", (), EPOCH))

    r = run_migrate(url)
    assert r.returncode == 0, r.stdout
    with connect(cluster, name) as c:
        assert state(c) == (True, False, True)
        assert c.execute("SELECT count(*) FROM kg.links WHERE kind = 3").fetchone()[0] == 1
    r = run_migrate(url, "downgrade", "kg_0013", PG_MIGRATE_ALLOW_DOWNGRADE="1")
    assert r.returncode == 0, r.stdout
    with connect(cluster, name) as c:
        assert state(c) == (True, True, True)
        assert [tuple(x) for x in c.execute("SELECT rule, object FROM kernel.rls_violations")] == []
        assert c.execute("SELECT count(*) FROM kernel.pin_violations").fetchone()[0] == 0
    r = run_migrate(url)
    assert r.returncode == 0, r.stdout
    with connect(cluster, name) as c:
        assert state(c) == (True, False, True)
        assert c.execute("SELECT count(DISTINCT proc) FROM kernel.vetted_write_pins WHERE rel::text LIKE 'kg.%'").fetchone()[0] == 29


def test_the_parent_and_grant_revision_moves_the_floor_and_its_downgrade_takes_it_back(db):
    """kg_0017: the projection writers refuse epoch 6 (code that read PARENT_AND_GRANT as an unknown rule) and the records writer takes the rule; down to kg_0016,
    the writers take epoch 6 again and the records writer keeps the rule (older code never writes it), the catalog is clean; an upgrade takes the floor back."""
    import psycopg

    from .rls_fixture import ORG_A as org
    from .test_kg_writers import EPOCH, call
    cluster, name = db
    url = cluster.url(name, "pipeshub_migrate")

    def state(c) -> tuple[bool, bool, int]:
        took = []
        for epoch in (6, EPOCH):
            try:
                call(c, "kg.w_changes_ack", org, (), epoch=epoch)
                took.append(True)
            except psycopg.errors.InvalidParameterValue:
                took.append(False)
        rule = c.execute("SELECT count(*) FROM pg_proc WHERE pronamespace = 'kg'::regnamespace AND proname = 'w_records_upsert' AND prosrc LIKE %s",
                         ("%'PARENT_AND_GRANT')%",)).fetchone()[0]
        return (*took, rule)

    r = run_migrate(url)
    assert r.returncode == 0, r.stdout
    with connect(cluster, name) as c:
        assert state(c) == (False, True, 1)
    r = run_migrate(url, "downgrade", "kg_0016", PG_MIGRATE_ALLOW_DOWNGRADE="1")
    assert r.returncode == 0, r.stdout
    with connect(cluster, name) as c:
        assert state(c) == (True, True, 1)
        assert [tuple(x) for x in c.execute("SELECT rule, object FROM kernel.rls_violations")] == []
        assert c.execute("SELECT count(*) FROM kernel.pin_violations").fetchone()[0] == 0
    r = run_migrate(url)
    assert r.returncode == 0, r.stdout
    with connect(cluster, name) as c:
        assert state(c) == (False, True, 1)
        assert c.execute("SELECT count(DISTINCT proc) FROM kernel.vetted_write_pins WHERE rel::text LIKE 'kg.%'").fetchone()[0] == 29
