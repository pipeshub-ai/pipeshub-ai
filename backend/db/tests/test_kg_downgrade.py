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
        assert c.execute("SELECT count(DISTINCT proc) FROM kernel.vetted_write_pins WHERE rel::text LIKE 'kg.%'").fetchone()[0] == 16


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
