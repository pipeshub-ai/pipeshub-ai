"""The kg schema (kg_0001): the typed tables are read-only to the graph services, every row belongs to one organisation, and the change log refuses an
empty entry. The services write only the change log (the capture triggers, kg_0004); the typed tables are written by the vetted writers alone."""
import psycopg
import pytest

from .test_integration import connect

TABLES = ("node_keys", "records", "links", "scope_members", "principal_closure", "scope_classes", "class_literals", "graph_changes", "projection_state")
READERS = ("pipeshub_connectors", "pipeshub_indexing", "pipeshub_query")


def test_the_graph_services_read_the_tables_and_nothing_can_write_them(rdb):
    cluster, name = rdb
    with connect(cluster, name) as c:
        for role in READERS:
            for t in TABLES:
                assert c.execute("SELECT has_table_privilege(%s, %s, 'SELECT')", (role, f"kg.{t}")).fetchone()[0], (role, t)
                for priv in ("INSERT", "UPDATE", "DELETE", "TRUNCATE"):
                    if (t, priv) == ("graph_changes", "INSERT"):      # the capture trigger inserts as the role that wrote the graph (kg_0004): the one write the services hold
                        assert c.execute("SELECT has_table_privilege(%s, %s, %s)", (role, f"kg.{t}", priv)).fetchone()[0], role
                        continue
                    assert not c.execute("SELECT has_table_privilege(%s, %s, %s)", (role, f"kg.{t}", priv)).fetchone()[0], (role, t, priv)
        # no column-level write either (a column grant does not show in the table privilege)
        for role in READERS:
            assert c.execute("SELECT count(*) FROM pg_attribute a WHERE a.attrelid = ANY(%s::regclass[]) AND a.attnum > 0 AND NOT a.attisdropped"
                             " AND (has_column_privilege(%s, a.attrelid, a.attnum, 'INSERT') OR has_column_privilege(%s, a.attrelid, a.attnum, 'UPDATE'))",
                             ([f"kg.{t}" for t in TABLES if t != "graph_changes"], role, role)).fetchone()[0] == 0, role


@pytest.mark.parametrize("role", ["pipeshub_node", "pipeshub_kv", "pipeshub_jobs"])
def test_other_services_hold_nothing_on_kg(rdb, role):
    cluster, name = rdb
    with connect(cluster, name) as c:
        assert not c.execute("SELECT has_schema_privilege(%s, 'kg', 'USAGE')", (role,)).fetchone()[0]
        for t in TABLES:
            assert not c.execute("SELECT has_table_privilege(%s, %s, 'SELECT, INSERT, UPDATE, DELETE')", (role, f"kg.{t}")).fetchone()[0], t


def test_every_table_has_a_row_policy_for_its_organisation(rdb):
    cluster, name = rdb
    with connect(cluster, name) as c:
        for t in TABLES:
            assert c.execute("SELECT relrowsecurity AND relforcerowsecurity FROM pg_class WHERE oid = %s::regclass", (f"kg.{t}",)).fetchone()[0], t
        assert [tuple(r) for r in c.execute("SELECT rule, object FROM kernel.rls_violations WHERE object LIKE 'kg.%'")] == []


def test_the_change_log_refuses_an_entry_that_names_no_node(rdb):
    cluster, name = rdb
    with connect(cluster, name) as c:
        with pytest.raises(psycopg.errors.CheckViolation):
            c.execute("INSERT INTO kg.graph_changes (org_id, ends) VALUES (%s, '{}')", ("a" * 24,))      # a valid org id: only the ends check can refuse it


def test_the_graph_carries_the_eight_capture_triggers_and_refuses_a_truncate(rdb):
    """Statement triggers on the partitioned parents (always enabled, so a replica session logs too); a truncate of either table is refused."""
    cluster, name = rdb
    with connect(cluster, name) as c:
        rows = {tuple(r) for r in c.execute("SELECT tgrelid::regclass::text, tgname, tgenabled FROM pg_trigger WHERE tgrelid IN ('graph.nodes'::regclass, 'graph.edges'::regclass) AND NOT tgisinternal")}
        assert rows == {(t, f"kg_{t.split('.')[1]}_{ev}", "A") for t in ("graph.nodes", "graph.edges") for ev in ("insert", "update", "delete", "truncate")}
        with pytest.raises(psycopg.errors.FeatureNotSupported):
            c.execute("TRUNCATE graph.edges")


def test_a_downgrade_takes_the_schema_away_and_an_upgrade_brings_it_back(db):
    from .conftest import run_migrate
    cluster, name = db
    url = cluster.url(name, "pipeshub_migrate")
    assert run_migrate(url).returncode == 0
    r = run_migrate(url, "downgrade", "kg@base", PG_MIGRATE_ALLOW_DOWNGRADE="1")
    assert r.returncode == 0, r.stdout
    with connect(cluster, name) as c:
        assert c.execute("SELECT to_regnamespace('kg')").fetchone()[0] is None
        assert [tuple(x) for x in c.execute("SELECT rule, object FROM kernel.rls_violations")] == []
    assert run_migrate(url).returncode == 0
