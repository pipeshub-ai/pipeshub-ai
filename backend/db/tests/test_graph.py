"""The graph schema (graph_0001): rows are per organisation, only the graph-reading services touch it, parallel edges are kept apart, and the
organisation and connector reads reach across organisations only through their vetted functions."""
import psycopg
import pytest

from .test_integration import connect

A, B = "a" * 24, "b" * 24
NODE = "INSERT INTO graph.nodes (org_id, coll, id, doc) VALUES (%s, %s, %s, %s::jsonb)"
EDGE = "INSERT INTO graph.edges (org_id, coll, from_coll, from_id, to_coll, to_id, ekey) VALUES (%s, 'permission', 'users', 'u1', 'records', 'r1', %s)"


def as_role(c, role, org=None):
    c.execute("RESET ROLE")
    c.execute(f"SET ROLE {role}")
    c.execute("SELECT set_config('app.org_id', %s, false)", (org or "",))


def test_an_organisation_sees_and_writes_only_its_own_rows(rdb):
    cluster, name = rdb
    with connect(cluster, name) as c:
        as_role(c, "pipeshub_connectors", A)
        c.execute(NODE, (A, "records", "r1", '{"id": "r1"}'))
        c.execute(EDGE, (A, "USER"))
        as_role(c, "pipeshub_connectors", B)
        assert c.execute("SELECT (SELECT count(*) FROM graph.nodes), (SELECT count(*) FROM graph.edges)").fetchone() == (0, 0)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):                 # row-level security rejects a row of another organisation
            c.execute(NODE, (A, "records", "r2", "{}"))
        c.execute("ROLLBACK")
        as_role(c, "pipeshub_connectors")                                          # no organisation: nothing is visible
        assert c.execute("SELECT count(*) FROM graph.nodes").fetchone()[0] == 0


@pytest.mark.parametrize("role,writes", [("pipeshub_connectors", True), ("pipeshub_indexing", True), ("pipeshub_query", True), ("pipeshub_jobs", False)])
def test_the_graph_services_can_read_and_only_the_writers_can_write(rdb, role, writes):
    cluster, name = rdb
    with connect(cluster, name) as c:
        as_role(c, role, A)
        c.execute("SELECT count(*) FROM graph.nodes")
        if writes:
            c.execute(NODE, (A, "apps", "app1", "{}"))
        else:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute(NODE, (A, "apps", "app1", "{}"))


@pytest.mark.parametrize("role", ["pipeshub_node", "pipeshub_kv"])
def test_other_services_hold_nothing_on_the_graph(rdb, role):
    cluster, name = rdb
    with connect(cluster, name) as c:
        as_role(c, role, A)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("SELECT 1 FROM graph.nodes")
        c.execute("ROLLBACK")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("SELECT 1 FROM graph.system_orgs(true, false, '', 10)")


def test_parallel_edges_of_one_pair_are_told_apart_by_their_key(rdb):
    cluster, name = rdb
    with connect(cluster, name) as c:
        as_role(c, "pipeshub_connectors", A)
        c.execute(EDGE, (A, "USER"))
        c.execute(EDGE, (A, "ORG"))
        assert c.execute("SELECT count(*) FROM graph.edges").fetchone()[0] == 2
        with pytest.raises(psycopg.errors.UniqueViolation):
            c.execute(EDGE, (A, "USER"))


def test_the_vetted_reads_list_organisations_and_connectors_across_organisations(rdb):
    cluster, name = rdb
    with connect(cluster, name) as c:
        for org in (A, B):
            as_role(c, "pipeshub_connectors", org)
            c.execute(NODE, (org, "organizations", org, f'{{"id": "{org}", "isActive": true}}'))
            c.execute(NODE, (org, "apps", f"app-{org[0]}", '{"isActive": true}'))
        as_role(c, "pipeshub_query")                                               # no organisation set
        assert [r[0] for r in c.execute("SELECT org_id FROM graph.system_orgs(false, false, '', 10)")] == [A, B]
        assert [r[0] for r in c.execute("SELECT org_id FROM graph.system_orgs(false, false, %s, 10)", (A,))] == [B]
        with pytest.raises(psycopg.errors.InsufficientPrivilege):                  # the query service has no connector read
            c.execute("SELECT 1 FROM graph.system_apps(true, '', 10)")
        c.execute("ROLLBACK")
        as_role(c, "pipeshub_indexing")
        assert [r[0] for r in c.execute("SELECT app_id FROM graph.system_apps(true, '', 10)")] == ["app-a", "app-b"]
        assert [r[0] for r in c.execute("SELECT org_id FROM graph.system_app('app-b')")] == [B]


def test_each_table_has_sixteen_hash_partitions_and_a_row_lands_in_one(rdb):
    cluster, name = rdb
    with connect(cluster, name) as c:
        for t in ("nodes", "edges"):
            leaves = c.execute("SELECT count(*) FROM pg_inherits i JOIN pg_class p ON p.oid = i.inhparent WHERE p.relname = %s", (f"{t}_def",)).fetchone()[0]
            assert leaves == 16
        as_role(c, "pipeshub_connectors", A)
        c.execute(NODE, (A, "records", "r1", "{}"))
        c.execute("RESET ROLE")
        assert c.execute("SELECT count(*) FROM (SELECT tableoid::regclass FROM graph.nodes) s WHERE s.tableoid::text ~ '^graph.nodes_h[0-9]{2}$'").fetchone()[0] == 1
