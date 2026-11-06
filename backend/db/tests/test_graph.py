"""The graph schema (graph_0001, with the hot lookups of graph_0003): rows are per organisation, only the graph-reading services touch it, parallel edges are kept apart, and the
organisation and connector reads reach across organisations only through their vetted functions."""
import json
import re

import psycopg
import pytest

from db import vetted_harness

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


def test_a_graph_that_holds_rows_upgrades_to_the_record_revision_index(db):
    """graph_0002 on a graph that already holds rows: the upgrade is one transaction, so the index is built inside it and the upgrade succeeds."""
    from .conftest import run_migrate
    cluster, name = db
    url = cluster.url(name, "pipeshub_migrate")
    assert run_migrate(url).returncode == 0
    r = run_migrate(url, "downgrade", "graph_0001", PG_MIGRATE_ALLOW_DOWNGRADE="1")
    assert r.returncode == 0, r.stdout
    with connect(cluster, name) as c:
        as_role(c, "pipeshub_connectors", A)
        c.execute(NODE, (A, "records", "r1", '{"id": "r1", "virtualRecordId": "v1", "contentRev": "1"}'))
        c.execute("COMMIT")
        assert c.execute("SELECT to_regclass('graph.nodes_record_virtual_rev')").fetchone()[0] is None
    r = run_migrate(url)
    assert r.returncode == 0, r.stdout + r.stderr
    with connect(cluster, name) as c:
        # every index of the tree, the root on graph.nodes included: a valid leaf set under an invalid root would still pass a count
        tree = "SELECT count(*), bool_and(i.indisvalid) FROM pg_partition_tree('graph.nodes_record_virtual_rev') t JOIN pg_index i ON i.indexrelid = t.relid"
        n, all_valid = c.execute(tree).fetchone()
        assert n == c.execute("SELECT count(*) FROM pg_partition_tree('graph.nodes')").fetchone()[0], "one index for every table of the tree"
        assert all_valid is True


# ---- the hot lookups (graph_0003) -------------------------------------------------------------------------------------------------------
# The provider's statement for the same read before the vetted functions (a null filter also matches an absent key when NULLABS).
PLAIN = """SELECT n.id FROM graph.nodes n WHERE n.org_id = current_setting('app.org_id') AND n.coll = %s AND {hot}
    AND NOT EXISTS (SELECT 1 FROM unnest(%s::text[], %s::text[]) f(k, v)
        WHERE CASE WHEN %s AND f.v = 'null' THEN coalesce(n.doc->f.k, 'null'::jsonb) <> 'null'::jsonb ELSE n.doc->f.k IS DISTINCT FROM f.v::jsonb END)
    ORDER BY n.id"""
HOT = {
    "blocks_by_record": ("blocks", "n.doc->>'recordId' = %s", "(%s, %s::text[], %s::jsonb[], %s, '', 1000)"),
    "blocks_by_name": ("blocks", "n.doc->>'connectorId' = %s AND n.doc->>'nameFolded' = %s", "(%s, %s, %s::text[], %s::jsonb[], %s, '', 1000)"),
    "code_files_by_path": ("codeFiles", "n.doc->>'filePath' = %s", "(%s, %s::text[], %s::jsonb[], %s, '', 1000)"),
}


def _block(i, org, rec="r1", conn="c1", **extra):
    return (org, "blocks", f"b{i}", json.dumps({"orgId": org, "recordId": rec, "connectorId": conn, "nameFolded": f"n{i % 3}", "qualifiedName": f"q{i}", **extra}))


def _seed_small(c):
    as_role(c, "pipeshub_connectors", A)
    rows = [_block(1, A), _block(2, A), _block(3, A, rec="r2"), _block(4, A, conn="c2"), _block(5, A, deprecated=None), _block(6, A, deprecated=False),
            (A, "blocks", "b7", json.dumps({"orgId": A, "recordId": "r1", "connectorId": "c1"})),                    # no nameFolded, qualifiedName
            (A, "codeFiles", "f1", json.dumps({"orgId": A, "filePath": "src/a.py"})), (A, "codeFiles", "f2", json.dumps({"orgId": A, "filePath": "src/b.py"}))]
    for r in rows:
        c.execute(NODE, r)
    as_role(c, "pipeshub_connectors", B)                                           # another org with the same ids and keys
    for r in [_block(1, B), _block(2, B), (B, "codeFiles", "f1", json.dumps({"orgId": B, "filePath": "src/a.py"}))]:
        c.execute(NODE, r)


# (function, hot values, extra filters as {key: json value}): every shape the code tools use, the null rule, absent keys and missing rows
CASES = [
    ("blocks_by_record", ("r1",), {"orgId": A, "recordId": "r1"}),
    ("blocks_by_record", ("r1",), {"orgId": A, "recordId": "r1", "qualifiedName": "q2", "connectorId": "c1"}),
    ("blocks_by_record", ("r1",), {"orgId": A, "recordId": "r1", "connectorId": "c1"}),
    ("blocks_by_record", ("r1",), {"orgId": B, "recordId": "r1"}),                                          # the other org's value on this org's rows
    ("blocks_by_record", ("r1",), {"recordId": "r1", "deprecated": None}),                                  # null: absent or null matches
    ("blocks_by_record", ("r1",), {"recordId": "r1", "deprecated": False}),
    ("blocks_by_record", ("r1",), {"recordId": "r1", "nameFolded": None}),                                  # a key absent on b7
    ("blocks_by_record", ("nope",), {"recordId": "nope"}),                                                  # a record with no blocks
    ("blocks_by_name", ("c1", "n1"), {"orgId": A, "connectorId": "c1", "nameFolded": "n1"}),
    ("blocks_by_name", ("c1", "n1"), {"connectorId": "c1", "nameFolded": "n1", "recordId": "r2"}),
    ("blocks_by_name", ("c2", "n1"), {"connectorId": "c2", "nameFolded": "n1"}),
    ("code_files_by_path", ("src/a.py",), {"orgId": A, "filePath": "src/a.py"}),
    ("code_files_by_path", ("src/a.py",), {"filePath": "src/a.py", "orgId": B}),
    ("code_files_by_path", ("src/zz.py",), {"filePath": "src/zz.py"}),
]


def test_a_hot_lookup_returns_what_the_plain_statement_returned_for_the_org_alone(rdb):
    """Both directions, for every shape the code tools use and each org: no row the statement did not return (another org's, one a filter excludes),
    and none missing (hot value, absent key, null filter, a missing row)."""
    cluster, name = rdb
    with connect(cluster, name) as c:
        _seed_small(c)
        for org in (A, B):
            as_role(c, "pipeshub_connectors", org)
            for null_absent in (True, False):
                for fn, lead, filters in CASES:
                    coll, hot, call = HOT[fn]
                    keys, vals = list(filters), [json.dumps(v) for v in filters.values()]
                    old = [r[0] for r in c.execute(PLAIN.format(hot=hot), (coll, *lead, keys, vals, null_absent))]
                    new = [r[0] for r in c.execute(f"SELECT node_id FROM graph.{fn}{call} ORDER BY node_id", (*lead, keys, vals, null_absent))]
                    assert new == old, (org, null_absent, fn, filters, new, old)
        assert any(c.execute(f"SELECT node_id FROM graph.{fn}{HOT[fn][2]}", (*lead, list(f), [json.dumps(v) for v in f.values()], True)).fetchall() for fn, lead, f in CASES)


def test_a_hot_lookup_answers_nothing_without_an_org_and_pages_after_an_id(rdb):
    cluster, name = rdb
    with connect(cluster, name) as c:
        _seed_small(c)
        for org in ("", "c" * 24):
            as_role(c, "pipeshub_connectors", org)
            assert c.execute("SELECT count(*) FROM graph.blocks_by_record('r1', '{}', '{}', true, '', 1000)").fetchone()[0] == 0
        as_role(c, "pipeshub_connectors", A)
        page = lambda after, n: [r[0] for r in c.execute("SELECT node_id FROM graph.blocks_by_record('r1', '{}', '{}', true, %s, %s)", (after, n))]  # noqa: E731
        assert page("", 1000) == ["b1", "b2", "b4", "b5", "b6", "b7"]
        assert page("", 2) == ["b1", "b2"] and page("b2", 2) == ["b4", "b5"] and page("b7", 2) == []


BAD_HOT = [
    (None, [], [], True, "", 10), ("", [], [], True, "", 10), ("x" * 201, [], [], True, "", 10), ("r", None, [], True, "", 10), ("r", [None], ["1"], True, "", 10),
    ("r", [""], ["1"], True, "", 10), ("r", ["k"] * 33, ["1"] * 33, True, "", 10), ("r", [], [], None, "", 10), ("r", [], [], True, None, 10),
    ("r", [], [], True, "x" * 201, 10), ("r", ["a"], ["1", "2"], True, "", 10), ("r", ["a", "b"], ["1"], True, "", 10), ("r", [], [], True, "", 0), ("r", [], [], True, "", 1001), ("r", [], [], True, "", None),
]


def test_a_hot_lookup_refuses_a_bad_argument(rdb):
    cluster, name = rdb
    with connect(cluster, name) as c:
        as_role(c, "pipeshub_connectors", A)
        for args in BAD_HOT:
            with pytest.raises(psycopg.errors.InvalidParameterValue):
                c.execute("SELECT * FROM graph.blocks_by_record(%s, %s::text[], %s::jsonb[], %s, %s, %s)", args)
            c.execute("ROLLBACK")


def test_only_the_graph_services_may_call_a_hot_lookup(rdb):
    cluster, name = rdb
    with connect(cluster, name) as c:
        for role, ok in (("pipeshub_connectors", True), ("pipeshub_indexing", True), ("pipeshub_query", True), ("pipeshub_node", False), ("pipeshub_jobs", False)):
            as_role(c, role, A)
            for fn in HOT:
                has = c.execute("SELECT has_function_privilege(%s, p.oid, 'EXECUTE') FROM pg_proc p WHERE p.pronamespace = 'graph'::regnamespace AND p.proname = %s", (role, fn)).fetchone()[0]
                assert has is ok, (role, fn)


def _uses(plan, index):
    return re.search(rf"Index (?:Only )?Scan(?: Backward)? using \S*{index}\b|Bitmap Index Scan on \S*{index}\b", plan) is not None


@pytest.mark.parametrize(("fn", "params", "index"), [
    ("graph.blocks_by_record(%s, %s::text[], %s::jsonb[], true)", ("rec7", ["recordId", "qualifiedName"], ['"rec7"', '"q777"']), "nodes_block_record"),
    ("graph.blocks_by_name(%s, %s, %s::text[], %s::jsonb[], true)", ("c1", "n7", ["connectorId"], ['"c1"']), "nodes_block_name"),
    ("graph.code_files_by_path(%s, %s::text[], %s::jsonb[], true)", ("src/p7/f7.py", ["orgId"], ['"' + "a" * 24 + '"']), "nodes_codefile_path"),
], ids=["blocks_by_record", "blocks_by_name", "code_files_by_path"])
def test_a_hot_lookup_uses_its_index_where_the_plain_statement_cannot(rdb, fn, params, index):
    """Row-level security keeps the planner from the expression index of a plain statement (the `->>` operator is not leakproof); the vetted function
    applies the org itself. Seven calls: the plpgsql plan cache goes generic after five."""
    cluster, name = rdb
    with connect(cluster, name) as c:
        as_role(c, "pipeshub_connectors", A)
        c.execute("INSERT INTO graph.nodes SELECT %s, 'blocks', 'b' || i, jsonb_build_object('orgId', %s::text, 'recordId', 'rec' || (i %% 100), 'connectorId', 'c' || (i %% 2),"
                  " 'nameFolded', 'n' || (i %% 500), 'qualifiedName', 'q' || i) FROM generate_series(1, 6000) i", (A, A))
        c.execute("INSERT INTO graph.nodes SELECT %s, 'codeFiles', 'f' || i, jsonb_build_object('orgId', %s::text, 'filePath', 'src/p' || (i %% 100) || '/f' || i || '.py')"
                  " FROM generate_series(1, 3000) i", (A, A))
        c.execute("RESET ROLE")
        c.execute("ANALYZE graph.nodes")
        plans = vetted_harness.plans_auto_explain(c, "pipeshub_connectors", A, f"SELECT node_id FROM {fn}", params)
        assert all(_uses(p, index) for p in plans[:1] + plans[-1:]), plans[0] + "\n" + plans[-1]
        assert all("Seq Scan on" not in p for p in plans), "a sequential scan of graph.nodes"
        # the same read as a plain statement, which is what the provider sent before: no key index
        coll, hot = ("codeFiles", "n.doc->>'filePath' = %s") if index == "nodes_codefile_path" else ("blocks", "n.doc->>'recordId' = %s")
        plain = vetted_harness.plans_auto_explain(c, "pipeshub_connectors", A, "SELECT n.id FROM graph.nodes n WHERE n.org_id = current_setting('app.org_id') AND n.coll = %s AND " + hot,
                                                  (coll, "src/p7/f7.py" if coll == "codeFiles" else "rec7"))
        assert not any(_uses(p, index) for p in plain), "the plain statement used the index: the function is not needed on this server"


def test_the_hot_lookups_keep_their_key_index_when_the_planner_expects_a_big_collection(rdb):
    """With ORDER BY id and a LIMIT in the one query, a planner that expects millions of rows (statistics are only estimates, and a partition can hold
    several orgs) walks the primary key in id order and filters the whole collection for a rare key. The matches are found by the key's index first."""
    cluster, name = rdb
    with connect(cluster, name) as c:
        as_role(c, "pipeshub_connectors", A)
        c.execute("INSERT INTO graph.nodes SELECT %s, 'blocks', 'b' || lpad(i::text, 6, '0'), jsonb_build_object('orgId', %s::text, 'connectorId', 'c1', 'nameFolded', 'n' || (i %% 50), "
                  "'recordId', CASE WHEN i < 3000 THEN 'common' ELSE 'rec' || (i %% 300) END) FROM generate_series(0, 11999) i", (A, A))
        c.execute("INSERT INTO graph.nodes SELECT %s, 'blocks', 'bz' || i, jsonb_build_object('orgId', %s::text, 'connectorId', 'c1', 'nameFolded', 'rare', 'recordId', 'rare') FROM generate_series(0, 2) i", (A, A))
        c.execute("INSERT INTO graph.nodes SELECT %s, 'codeFiles', 'f' || lpad(i::text, 6, '0'), jsonb_build_object('orgId', %s::text, "
                  "'filePath', CASE WHEN i < 2000 THEN 'src/common.py' ELSE 'src/p' || i || '.py' END) FROM generate_series(0, 11999) i", (A, A))
        c.execute("INSERT INTO graph.nodes VALUES (%s, 'codeFiles', 'fzz', %s::jsonb)", (A, json.dumps({"orgId": A, "filePath": "src/rare.py"})))
        c.execute("RESET ROLE")
        c.execute("ANALYZE graph.nodes")
        leaf = c.execute("SELECT tableoid::regclass::text FROM graph.nodes WHERE org_id = %s LIMIT 1", (A,)).fetchone()[0]
        # the planner now expects a partition of 4 billion documents (the table and its indexes: their density is what it scales to the pages it finds)
        c.execute(f"UPDATE pg_class SET reltuples = 4000000000 WHERE oid = '{leaf}'::regclass OR oid IN (SELECT indexrelid FROM pg_index WHERE indrelid = '{leaf}'::regclass)")
        cases = [
            ("graph.blocks_by_record(%s, %s::text[], %s::jsonb[], true)", ("rare", ["orgId", "recordId"], [f'"{A}"', '"rare"']), "recordId"),
            ("graph.blocks_by_record(%s, %s::text[], %s::jsonb[], true)", ("common", ["orgId", "recordId"], [f'"{A}"', '"common"']), "recordId"),
            ("graph.blocks_by_record(%s, %s::text[], %s::jsonb[], true)", ("nope", ["orgId", "recordId"], [f'"{A}"', '"nope"']), "recordId"),
            ("graph.blocks_by_name(%s, %s, %s::text[], %s::jsonb[], true)", ("c1", "rare", ["orgId", "connectorId", "nameFolded"], [f'"{A}"', '"c1"', '"rare"']), "nameFolded"),
            ("graph.code_files_by_path(%s, %s::text[], %s::jsonb[], true)", ("src/rare.py", ["orgId", "filePath"], [f'"{A}"', '"src/rare.py"']), "filePath"),
            ("graph.code_files_by_path(%s, %s::text[], %s::jsonb[], true)", ("src/common.py", ["orgId", "filePath"], [f'"{A}"', '"src/common.py"']), "filePath"),
            ("graph.code_files_by_path(%s, %s::text[], %s::jsonb[], true)", ("src/none.py", ["orgId", "filePath"], [f'"{A}"', '"src/none.py"']), "filePath"),
        ]
        for call, params, key in cases:
            plans = vetted_harness.plans_auto_explain(c, "pipeshub_connectors", A, f"SELECT node_id FROM {call}", params)
            for i in (0, 6):
                assert re.search(rf"Index Cond: .*\(doc ->> '{key}'::text\)", plans[i]), (call, params[0], i, plans[i])
        # and the answers are unchanged: ascending by id, after the given id
        as_role(c, "pipeshub_connectors", A)
        assert [r[0] for r in c.execute("SELECT node_id FROM graph.blocks_by_record('rare', '{}', '{}', true, '', 1000)")] == ["bz0", "bz1", "bz2"]
        assert [r[0] for r in c.execute("SELECT node_id FROM graph.blocks_by_record('rare', '{}', '{}', true, 'bz0', 1)")] == ["bz1"]
