"""Taking the kg writers away (a development downgrade to kg_0001) leaves a database the catalog check accepts, and an upgrade brings them back."""
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
        assert c.execute("SELECT count(DISTINCT proc) FROM kernel.vetted_write_pins WHERE rel::text LIKE 'kg.%'").fetchone()[0] == 25


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
