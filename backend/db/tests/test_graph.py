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
    """Each grant is checked by itself (a table grant without the schema grant would be masked by the missing USAGE)."""
    cluster, name = rdb
    with connect(cluster, name) as c:
        assert not c.execute("SELECT has_schema_privilege(%s, 'graph', 'USAGE, CREATE')", (role,)).fetchone()[0]
        for rel in ("graph.nodes", "graph.edges"):
            assert not c.execute("SELECT has_table_privilege(%s, %s, 'SELECT, INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER')", (role, rel)).fetchone()[0]
        for fn in c.execute("SELECT p.oid::regprocedure::text FROM pg_proc p WHERE p.pronamespace = 'graph'::regnamespace").fetchall():
            assert not c.execute("SELECT has_function_privilege(%s, %s, 'EXECUTE')", (role, fn[0])).fetchone()[0], fn


def test_an_edge_of_another_organisation_is_refused(rdb):
    cluster, name = rdb
    with connect(cluster, name) as c:
        as_role(c, "pipeshub_connectors", B)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute(EDGE, (A, "USER"))


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
        assert [r[0] for r in c.execute("SELECT org_id FROM graph.system_orgs(false, false, '', '', 10)")] == [A, B]
        assert [r[0] for r in c.execute("SELECT org_id FROM graph.system_orgs(false, false, %s, %s, 10)", (A, A))] == [B]
        with pytest.raises(psycopg.errors.InsufficientPrivilege):                  # the query service has no connector read
            c.execute("SELECT 1 FROM graph.system_apps(true, '', '', 10)")
        c.execute("ROLLBACK")
        as_role(c, "pipeshub_indexing")
        assert [r[0] for r in c.execute("SELECT app_id FROM graph.system_apps(true, '', '', 10)")] == ["app-a", "app-b"]
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


def _seed(c, org, coll, id_, doc="{}"):
    as_role(c, "pipeshub_connectors", org)
    c.execute(NODE, (org, coll, id_, doc))


def test_connector_instances_page_by_id_and_organisation_when_an_id_repeats(rdb):
    """An app id is unique per organisation only: a page of one row must continue after the (id, org) pair, not after the id."""
    cluster, name = rdb
    with connect(cluster, name) as c:
        for org, id_ in ((A, "dup"), (B, "dup"), (A, "zed")):
            _seed(c, org, "apps", id_, '{"isActive": true}')
        _seed(c, B, "apps", "off", '{"isActive": false}')
        as_role(c, "pipeshub_indexing")
        seen, cursor = [], ("", "")
        while rows := c.execute("SELECT org_id, app_id FROM graph.system_apps(true, %s, %s, 1)", cursor).fetchall():
            seen += [tuple(r) for r in rows]
            cursor = (rows[-1][1], rows[-1][0])
        assert seen == [(A, "dup"), (B, "dup"), (A, "zed")]
        assert {r[1] for r in c.execute("SELECT org_id, app_id FROM graph.system_apps(false, '', '', 10)")} == {"dup", "off", "zed"}


def test_organisations_page_by_org_and_id_and_filter_on_the_flags(rdb):
    cluster, name = rdb
    with connect(cluster, name) as c:
        _seed(c, A, "organizations", A, '{"isActive": true}')
        _seed(c, A, "organizations", "second", '{"isActive": true}')          # the key allows more than one organisation document per org
        _seed(c, B, "organizations", B, '{"isActive": false}')
        _seed(c, "c" * 24, "organizations", "c" * 24, '{"isActive": true, "isExternal": true}')
        as_role(c, "pipeshub_query")
        seen, cursor = [], ("", "")
        while rows := c.execute("SELECT org_id, node_id FROM graph.system_orgs(false, false, %s, %s, 1)", cursor).fetchall():
            seen += [tuple(r) for r in rows]
            cursor = (rows[-1][0], rows[-1][1])
        assert seen == sorted([(A, A), (A, "second")])                              # live and internal only
        assert [r[0] for r in c.execute("SELECT org_id FROM graph.system_orgs(true, false, '', '', 10)")] == [A, A, B]
        assert [r[0] for r in c.execute("SELECT org_id FROM graph.system_orgs(false, true, '', '', 10)")] == ["c" * 24]


LONG = "x" * 201
BAD_ARGS = [
    ("graph.system_orgs", "(%s, %s, %s, %s, %s)", args) for args in (
        (None, False, "", "", 10), (True, None, "", "", 10), (True, False, None, "", 10), (True, False, "", None, 10),
        (True, False, LONG, "", 10), (True, False, "", LONG, 10), (True, False, "", "", 0), (True, False, "", "", 1001), (True, False, "", "", None))
] + [
    ("graph.system_apps", "(%s, %s, %s, %s)", args) for args in (
        (None, "", "", 10), (True, None, "", 10), (True, "", None, 10), (True, LONG, "", 10), (True, "", LONG, 10),
        (True, "", "", 0), (True, "", "", 1001), (True, "", "", None))
] + [
    ("graph.system_app", "(%s)", args) for args in ((None,), ("",), (LONG,))
]


@pytest.mark.parametrize(("fn", "marks", "args"), BAD_ARGS, ids=[f"{f.removeprefix('graph.')}-{i}" for i, (f, _, _) in enumerate(BAD_ARGS)])
def test_a_function_refuses_a_bad_argument_under_an_authorised_role(rdb, fn, marks, args):
    """A null, an over-long cursor or id and a limit outside 1..1000 are refused with invalid_parameter_value, not answered."""
    cluster, name = rdb
    with connect(cluster, name) as c:
        as_role(c, "pipeshub_indexing")
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            c.execute(f"SELECT * FROM {fn}{marks}", args)
