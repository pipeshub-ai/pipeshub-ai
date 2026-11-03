"""The kg schema (kg_0001): the typed tables are read-only to the services, every row belongs to one organisation, and a write to the document store is
logged for its organisation, in the writer's own transaction, by statement-level triggers."""
import psycopg
import pytest

from .test_graph import EDGE, NODE, A, B, as_role
from .test_integration import connect

TABLES = ("node_keys", "records", "links", "scope_members", "principal_closure", "scope_classes", "class_literals", "graph_changes", "projection_state")
RECORD = '{"id": "r1", "connectorId": "c1", "recordName": "One"}'


def changes(c):
    return sorted((r[0], r[1], sorted(r[2])) for r in c.execute("SELECT org_id, app_id, ends FROM kg.graph_changes"))


def test_the_services_read_the_tables_and_cannot_write_them(rdb):
    cluster, name = rdb
    with connect(cluster, name) as c:
        for role in ("pipeshub_connectors", "pipeshub_indexing", "pipeshub_query", "pipeshub_jobs"):
            for t in TABLES:
                assert c.execute("SELECT has_table_privilege(%s, %s, 'SELECT')", (role, f"kg.{t}")).fetchone()[0], (role, t)
                for priv in ("UPDATE", "DELETE", "TRUNCATE"):
                    assert not c.execute("SELECT has_table_privilege(%s, %s, %s)", (role, f"kg.{t}", priv)).fetchone()[0], (role, t, priv)
                insert = c.execute("SELECT has_table_privilege(%s, %s, 'INSERT')", (role, f"kg.{t}")).fetchone()[0]
                assert insert == (t == "graph_changes" and role != "pipeshub_jobs"), (role, t)       # only the capture trigger inserts, as the writer of the graph


@pytest.mark.parametrize("role", ["pipeshub_node", "pipeshub_kv"])
def test_other_services_hold_nothing_on_kg(rdb, role):
    cluster, name = rdb
    with connect(cluster, name) as c:
        assert not c.execute("SELECT has_schema_privilege(%s, 'kg', 'USAGE')", (role,)).fetchone()[0]


def test_every_table_has_a_row_policy_for_its_organisation(rdb):
    cluster, name = rdb
    with connect(cluster, name) as c:
        for t in TABLES:
            assert c.execute("SELECT relrowsecurity AND relforcerowsecurity FROM pg_class WHERE oid = %s::regclass", (f"kg.{t}",)).fetchone()[0], t
        assert [tuple(r) for r in c.execute("SELECT rule, object FROM kernel.rls_violations WHERE object LIKE 'kg.%'")] == []


def test_a_node_written_to_the_graph_is_logged_for_its_organisation_and_connector(rdb):
    cluster, name = rdb
    with connect(cluster, name) as c:
        as_role(c, "pipeshub_connectors", A)
        c.execute(NODE, (A, "records", "r1", RECORD))
        c.execute(NODE, (A, "users", "u1", "{}"))
        c.execute(NODE, (A, "blocks", "b1", "{}"))                                  # a collection kg does not project
        assert changes(c) == [(A, "", ["4:u1"]), (A, "c1", ["3:r1"])]
        as_role(c, "pipeshub_connectors", B)
        assert changes(c) == []                                                      # another organisation sees none of it
        c.execute("ROLLBACK")


def test_an_update_is_logged_only_when_what_the_projection_reads_changed(rdb):
    cluster, name = rdb
    with connect(cluster, name) as c:
        as_role(c, "pipeshub_connectors", A)
        c.execute(NODE, (A, "records", "r1", RECORD))
        c.execute("COMMIT")
    with connect(cluster, name) as c:
        as_role(c, "pipeshub_connectors", A)
        before = changes(c)
        c.execute("UPDATE graph.nodes SET doc = doc || '{\"indexingStatus\": \"DONE\"}' WHERE id = 'r1'")
        assert changes(c) == before                                                  # a status flip is not a change to the projected fields
        c.execute("UPDATE graph.nodes SET doc = doc || '{\"recordName\": \"Two\"}' WHERE id = 'r1'")
        assert changes(c) == sorted([*before, (A, "c1", ["3:r1"])])
        c.execute("ROLLBACK")


def test_an_edge_is_logged_for_the_end_its_row_depends_on(rdb):
    cluster, name = rdb
    with connect(cluster, name) as c:
        as_role(c, "pipeshub_connectors", A)
        c.execute(EDGE, (A, "USER"))                                                 # a grant from a user on a record: the record's row changes
        assert changes(c) == [(A, "", ["3:r1"])]
        c.execute("ROLLBACK")


def test_the_log_rolls_back_with_the_write(rdb):
    cluster, name = rdb
    with connect(cluster, name) as c:
        as_role(c, "pipeshub_connectors", A)
        c.execute("BEGIN")
        c.execute(NODE, (A, "records", "r1", RECORD))
        assert changes(c) == [(A, "c1", ["3:r1"])]                                   # logged in the writer's own transaction
        c.execute("ROLLBACK")
        assert changes(c) == []


@pytest.mark.parametrize("table", ["nodes", "edges"])
def test_truncating_the_graph_is_refused(rdb, table):
    cluster, name = rdb
    with connect(cluster, name) as c:
        with pytest.raises(psycopg.errors.FeatureNotSupported):
            c.execute(f"TRUNCATE graph.{table}")
        c.execute("ROLLBACK")


def test_the_triggers_fire_when_replication_triggers_are_off(rdb):
    cluster, name = rdb
    with connect(cluster, name) as c:
        assert sorted(r[0] for r in c.execute(
            "SELECT tgname FROM pg_trigger WHERE tgrelid IN ('graph.nodes'::regclass, 'graph.edges'::regclass) AND tgname LIKE 'kg\\_%' AND tgenabled = 'A'")) == sorted(
            f"kg_{t}_{e}" for t in ("nodes", "edges") for e in ("insert", "update", "delete", "truncate"))


def test_a_downgrade_takes_the_schema_and_the_triggers_away_and_an_upgrade_brings_them_back(db):
    from .conftest import run_migrate
    cluster, name = db
    url = cluster.url(name, "pipeshub_migrate")
    assert run_migrate(url).returncode == 0
    r = run_migrate(url, "downgrade", "kg@base", PG_MIGRATE_ALLOW_DOWNGRADE="1")
    assert r.returncode == 0, r.stdout
    with connect(cluster, name) as c:
        assert c.execute("SELECT to_regnamespace('kg')").fetchone()[0] is None
        assert c.execute("SELECT count(*) FROM pg_trigger WHERE tgname LIKE 'kg\\_%'").fetchone()[0] == 0
        assert [tuple(x) for x in c.execute("SELECT rule, object FROM kernel.rls_violations")] == []
    assert run_migrate(url).returncode == 0
