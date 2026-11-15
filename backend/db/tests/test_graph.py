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


# ---- the lookups of graph_0004 -----------------------------------------------------------------------------------------------------------
# (function, collection, the old statement's predicate on n.doc, sample arguments, the key its index leads with after org_id)
LOOKUPS = {
    "record_by_external": ("records", "n.doc->>'connectorId' = %s AND n.doc->>'externalRecordId' = %s", ("app1", "e1"), "externalRecordId"),
    "user_by_user_id": ("users", "n.doc->>'userId' = %s", ("x",), "userId"),
}


def _seed_lookups(c):
    for org in (A, B):
        as_role(c, "pipeshub_connectors", org)
        for coll, key in (("records", "externalRecordId"),):
            for i, (conn, ext) in enumerate((("app1", "e1"), ("app1", "e2"), ("app2", "e1"))):
                c.execute(NODE, (org, coll, f"{coll[0]}{i}", json.dumps({"connectorId": conn, key: ext, "orgId": org})))
        c.execute(NODE, (org, "records", "rdel", json.dumps({"connectorId": "app1", "externalRecordId": "e1", "isDeleted": True, "orgId": org})))     # the same pair, deleted
        c.execute(NODE, (org, "records", "rnone", json.dumps({"orgId": org})))                                                                           # no key at all
        for i, uid in enumerate(("x", "y")):
            c.execute(NODE, (org, "users", f"u{i}", json.dumps({"userId": uid})))
    as_role(c, "pipeshub_connectors", A)
    c.execute(NODE, (A, "users", "uA", json.dumps({"userId": "only-a"})))


def test_a_lookup_of_graph_0004_returns_what_the_plain_statement_returned_for_the_org_alone(rdb):
    cluster, name = rdb
    with connect(cluster, name) as c:
        _seed_lookups(c)
        for org in (A, B):
            as_role(c, "pipeshub_connectors", org)
            for fn, (coll, pred, args, _key) in LOOKUPS.items():
                n = pred.count("%s")
                probes = [args, ("app2", "e1")[-n:] if n == 2 else ("y",), ("nope",) * n, ("only-a",) if n == 1 else ("app1", "only-a")]
                for probe in probes:
                    old = sorted(r[0] for r in c.execute(f"SELECT n.id FROM graph.nodes n WHERE n.org_id = current_setting('app.org_id') AND n.coll = '{coll}' AND {pred}", probe))
                    new = sorted(r[0] for r in c.execute(f"SELECT node_id FROM graph.{fn}({', '.join(['%s'] * n)})", probe))
                    assert new == old, (org, fn, probe, new, old)
        # both directions spelled out: the pair that two records hold is found with both, and A's own user never reaches B
        as_role(c, "pipeshub_connectors", A)
        assert sorted(r[0] for r in c.execute("SELECT node_id FROM graph.record_by_external('app1', 'e1')")) == ["r0", "rdel"]
        assert [r[0] for r in c.execute("SELECT node_id FROM graph.user_by_user_id('only-a')")] == ["uA"]
        as_role(c, "pipeshub_connectors", B)
        assert c.execute("SELECT count(*) FROM graph.user_by_user_id('only-a')").fetchone()[0] == 0
        for org in ("", "c" * 24):
            as_role(c, "pipeshub_connectors", org)
            for fn, (_coll, pred, args, _k) in LOOKUPS.items():
                assert c.execute(f"SELECT count(*) FROM graph.{fn}({', '.join(['%s'] * pred.count('%s'))})", args).fetchone()[0] == 0


def test_the_virtual_record_lookup_returns_the_records_of_the_org_that_hold_a_virtual_record_id(rdb):
    cluster, name = rdb
    docs = {"a1": {"virtualRecordId": "v1", "orgId": A}, "a2": {"virtualRecordId": "v1", "orgId": A, "isDeleted": True}, "a3": {"virtualRecordId": "v2", "orgId": A},
            "a4": {"virtualRecordId": "v2", "orgId": "other"}, "a5": {"virtualRecordId": "v3"}, "a6": {"orgId": A}}
    with connect(cluster, name) as c:
        for org in (A, B):
            as_role(c, "pipeshub_connectors", org)
            for k, d in docs.items():
                c.execute(NODE, (org, "records", f"{org[0]}{k}", json.dumps({**d, **({"orgId": org} if d.get("orgId") == A else {})})))
            c.execute(NODE, (org, "recordGroups", f"{org[0]}g", json.dumps({"virtualRecordId": "v1", "orgId": org})))        # not a record
        for org in (A, B):
            as_role(c, "pipeshub_connectors", org)
            for probe in (["v1"], ["v1", "v2", "v3"], ["nope"], [], ["v1", "v1"]):
                old = sorted(r[0] for r in c.execute("SELECT n.id FROM graph.nodes n WHERE n.org_id = current_setting('app.org_id') AND n.coll = 'records' "
                                                     "AND n.doc->>'orgId' = n.org_id AND n.doc->>'virtualRecordId' = ANY(%s::text[])", (probe,)))
                new = sorted(r[0] for r in c.execute("SELECT node_id FROM graph.records_by_virtual_id(%s::text[])", (probe,)))
                assert new == old, (org, probe, new, old)
            assert sorted(r[0] for r in c.execute("SELECT node_id FROM graph.records_by_virtual_id('{v1,v2}')")) == [f"{org[0]}a1", f"{org[0]}a2", f"{org[0]}a3"]
        as_role(c, "pipeshub_connectors")
        assert c.execute("SELECT count(*) FROM graph.records_by_virtual_id('{v1}')").fetchone()[0] == 0


BAD_LOOKUP = [("record_by_external", "(%s, %s)", a) for a in ((None, "e"), ("", "e"), ("c" * 201, "e"), ("c", None), ("c", ""), ("c", "e" * 2001))] + [
    ("user_by_user_id", "(%s)", a) for a in ((None,), ("",), ("u" * 201,))] + [
    ("records_by_virtual_id", "(%s::text[])", a) for a in ((None,), ([None],), ([""],), (["v" * 201],), (["v"] * 1001,))]


def test_a_lookup_of_graph_0004_refuses_a_bad_argument_and_only_the_graph_services_may_call_it(rdb):
    cluster, name = rdb
    with connect(cluster, name) as c:
        as_role(c, "pipeshub_connectors", A)
        for fn, marks, args in BAD_LOOKUP:
            with pytest.raises(psycopg.errors.InvalidParameterValue):
                c.execute(f"SELECT * FROM graph.{fn}{marks}", args)
            c.execute("ROLLBACK")
        names = [*LOOKUPS, "records_by_virtual_id"]
        for role, ok in (("pipeshub_connectors", True), ("pipeshub_indexing", True), ("pipeshub_query", True), ("pipeshub_node", False), ("pipeshub_jobs", False)):
            for fn in names:
                has = c.execute("SELECT has_function_privilege(%s, p.oid, 'EXECUTE') FROM pg_proc p WHERE p.pronamespace = 'graph'::regnamespace AND p.proname = %s", (role, fn)).fetchone()[0]
                assert has is ok, (role, fn)


def test_the_lookups_of_graph_0004_use_their_index_where_the_plain_statement_cannot(rdb):
    """Row-level security leaves a plain statement the org_id prefix of the expression index; the function applies the org itself and the index condition
    names the key. Seven calls: the plpgsql plan cache goes generic after five."""
    cluster, name = rdb
    with connect(cluster, name) as c:
        as_role(c, "pipeshub_connectors", A)
        c.execute("INSERT INTO graph.nodes SELECT %s, 'records', 'r' || i, jsonb_build_object('orgId', %s::text, 'connectorId', 'app1', 'externalRecordId', 'e' || i, 'virtualRecordId', 'v' || i) "
                  "FROM generate_series(1, 4000) i", (A, A))
        c.execute("INSERT INTO graph.nodes SELECT %s, 'users', 'u' || i, jsonb_build_object('userId', 'x' || i) FROM generate_series(1, 20000) i", (A,))
        c.execute("RESET ROLE")
        c.execute("ANALYZE graph.nodes")
        cases = [
            ("record_by_external", "graph.record_by_external(%s, %s)", ("app1", "e777"), "externalRecordId"),
            ("user_by_user_id", "graph.user_by_user_id(%s)", ("x777",), "userId"),
            ("records_by_virtual_id", "graph.records_by_virtual_id(%s::text[])", (["v1", "v777", "v3000"],), "virtualRecordId"),
        ]
        for fn, call, params, key in cases:
            plans = vetted_harness.plans_auto_explain(c, "pipeshub_connectors", A, f"SELECT node_id FROM {call}", params)
            for i in (0, 6):                 # the first (custom) and the seventh call
                assert re.search(rf"Index Cond: .*\(doc ->> '{key}'::text\)", plans[i]), (fn, i, plans[i])
                assert "Seq Scan on" not in plans[i], (fn, i)
        # the plain statements the provider sent before: the index is reached by org_id alone
        coll_of = {"record_by_external": "records", "user_by_user_id": "users", "records_by_virtual_id": "records"}
        old = {"record_by_external": ("n.doc->>'externalRecordId' = %s", ("e777",), "externalRecordId"), "user_by_user_id": ("n.doc->>'userId' = %s", ("x777",), "userId")}
        for fn, (pred, params, key) in old.items():
            plain = vetted_harness.plans_auto_explain(c, "pipeshub_connectors", A, f"SELECT n.id FROM graph.nodes n WHERE n.org_id = current_setting('app.org_id') AND n.coll = '{coll_of[fn]}' AND {pred}", params)
            assert not any(re.search(rf"Index Cond: .*\(doc ->> '{key}'::text\)", p) for p in plain), plain[0]


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
        c.execute("INSERT INTO graph.nodes SELECT %s, 'users', 'u' || i, jsonb_build_object('userId', 'x' || i) FROM generate_series(1, 12000) i", (A,))
        c.execute("INSERT INTO graph.nodes SELECT %s, 'records', 'r' || i, jsonb_build_object('orgId', %s::text, 'connectorId', 'app1', 'externalRecordId', 'e' || i, "
                  "'virtualRecordId', 'v' || i) FROM generate_series(1, 12000) i", (A, A))
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
            ("graph.user_by_user_id(%s)", ("x777",), "userId"),
            ("graph.user_by_user_id(%s)", ("nobody",), "userId"),
            ("graph.record_by_external(%s, %s)", ("app1", "e777"), "externalRecordId"),
            ("graph.record_by_external(%s, %s)", ("app1", "nope"), "externalRecordId"),
            ("graph.records_by_virtual_id(%s::text[])", (["v1", "v777", "v11999"],), "virtualRecordId"),
            ("graph.records_by_virtual_id(%s::text[])", (["nope"],), "virtualRecordId"),
        ]
        for call, params, key in cases:
            plans = vetted_harness.plans_auto_explain(c, "pipeshub_connectors", A, f"SELECT node_id FROM {call}", params)
            for i in (0, 6):
                assert re.search(rf"Index Cond: .*\(doc ->> '{key}'::text\)", plans[i]), (call, params[0], i, plans[i])
        # and the answers are unchanged: ascending by id, after the given id
        as_role(c, "pipeshub_connectors", A)
        assert [r[0] for r in c.execute("SELECT node_id FROM graph.blocks_by_record('rare', '{}', '{}', true, '', 1000)")] == ["bz0", "bz1", "bz2"]
        assert [r[0] for r in c.execute("SELECT node_id FROM graph.blocks_by_record('rare', '{}', '{}', true, 'bz0', 1)")] == ["bz1"]


# ---- the knowledge graph's per-record lookups (graph_0006) ------------------------------------------------------------------------------
def _seed_kg(c):
    """The same ids and keys in both orgs, awkward ones included: mixed-case and non-ASCII ids (byte order is not the database's order), repeated source keys and
    emails in other cases, blanks and wildcard characters in names, a person with no name."""
    for org in (A, B):
        as_role(c, "pipeshub_connectors", org)
        for i in ("a1", "B1", "_x", "éa", "Zed", "z9", "c2only"):
            c.execute(NODE, (org, "records", i, json.dumps({"connectorId": "c2" if i == "c2only" else "c1", "orgId": org})))
        c.execute(NODE, (org, "records", "norec", json.dumps({"orgId": org})))                                              # no connectorId
        for i, (key, ext, parent, norm) in enumerate((("acme", True, org, "acme"), ("acme", False, org, "acme"), ("acme", True, "other", "acme"), ("beta", True, org, "beta"), ("acme", True, org, "acme"))):
            c.execute(NODE, (org, "organizations", f"o{i}", json.dumps({"normalizedName": norm, "isExternal": ext, "parentOrgId": parent, "name": key})))
        for pid, doc in (("p1", {"sourceKey": "c1:u1", "fullName": "Ann Lee", "email": "Ann@X.com"}), ("p0", {"sourceKey": "c1:u1", "fullName": "Ann  Lee ", "email": "ann@x.com"}),
                         ("p2", {"sourceKey": "c1:u2", "fullName": "Annabel Stone", "firstName": "Anna", "lastName": "Stone", "email": "stone@x.com"}),
                         ("p3", {"fullName": "100% Joe_Smith\\", "email": "JOE@x.com"}), ("p4", {"firstName": "Lee", "lastName": "Ann"}), ("p5", {"email": "none@x.com"}),
                         ("p6", {"fullName": "Mary Ann Smith"}), ("p7", {"fullName": "ANN"})):
            c.execute(NODE, (org, "person", pid, json.dumps(doc)))
        for gid, email in (("g1", "Team@X.com"), ("g2", "ann@x.com"), ("g3", None)):
            c.execute(NODE, (org, "groups", gid, json.dumps({"email": email} if email else {"name": "n"})))
        for uid, email in (("u1", "Ann@X.com"), ("u2", "other@x.com")):
            c.execute(NODE, (org, "users", uid, json.dumps({"email": email, "userId": uid})))
        c.execute(NODE, (org, "recordGroups", "rg", json.dumps({"email": "ann@x.com", "connectorId": "c1", "sourceKey": "c1:u1", "normalizedName": "acme"})))      # another collection
    as_role(c, "pipeshub_connectors", A)
    c.execute(NODE, (A, "person", "pa", json.dumps({"fullName": "Only A", "email": "onlya@x.com", "sourceKey": "c1:only-a"})))


def _ids(c, sql, *args):
    return [tuple(r) if len(r) > 1 else r[0] for r in c.execute(sql, args[0] if args and isinstance(args[0], dict) else args)]


def test_the_records_of_a_connector_page_in_byte_order_as_the_plain_statement_did(rdb):
    cluster, name = rdb
    with connect(cluster, name) as c:
        _seed_kg(c)
        plain = """SELECT n.id FROM graph.nodes n WHERE n.org_id = current_setting('app.org_id') AND n.coll = 'records' AND n.doc->>'connectorId' = %s
                   AND (%s::text IS NULL OR n.id COLLATE "C" > %s::text COLLATE "C") ORDER BY n.id COLLATE "C" LIMIT %s"""
        for org in (A, B):
            as_role(c, "pipeshub_connectors", org)
            for conn in ("c1", "c2", "nope"):
                for after in (None, "B1", "Zed", "zzz", "_x"):
                    for n in (1, 2, 1000):
                        old = _ids(c, plain, conn, after, after, n)
                        assert _ids(c, "SELECT node_id FROM graph.records_page_by_connector(%s, %s, %s)", conn, after or "", n) == old, (org, conn, after, n)
        as_role(c, "pipeshub_connectors", A)
        assert _ids(c, "SELECT node_id FROM graph.records_page_by_connector('c1', '', 1000)") == ["B1", "Zed", "_x", "a1", "z9", "éa"]
        assert _ids(c, "SELECT node_id FROM graph.records_page_by_connector('c1', 'Zed', 2)") == ["_x", "a1"]


def test_organisations_by_name_return_what_the_plain_statement_returned(rdb):
    cluster, name = rdb
    plain = """SELECT id FROM graph.nodes WHERE org_id = current_setting('app.org_id') AND coll = 'organizations' AND doc->>'parentOrgId' = %s
               AND doc->>'normalizedName' = ANY(%s::text[]) AND doc->'isExternal' = 'true'::jsonb ORDER BY id COLLATE "C" """
    with connect(cluster, name) as c:
        _seed_kg(c)
        for org in (A, B):
            as_role(c, "pipeshub_connectors", org)
            for parent in (org, A, B, "other"):
                for names in (["acme"], ["beta", "acme"], ["nope"], [], ["acme", "acme"]):
                    assert sorted(_ids(c, "SELECT node_id FROM graph.organizations_by_name(%s, %s::text[])", parent, names)) == _ids(c, plain, parent, names), (org, parent, names)
        as_role(c, "pipeshub_connectors", A)
        assert sorted(_ids(c, "SELECT node_id FROM graph.organizations_by_name(%s, '{acme,beta}')", A)) == ["o0", "o3", "o4"]


def test_a_person_by_source_key_is_the_lowest_id_of_the_org_alone(rdb):
    cluster, name = rdb
    with connect(cluster, name) as c:
        _seed_kg(c)
        for org in (A, B):
            as_role(c, "pipeshub_connectors", org)
            for key in ("c1:u1", "c1:u2", "c1:only-a", "C1:U1", "nope"):
                old = _ids(c, "SELECT id FROM graph.nodes WHERE org_id = current_setting('app.org_id') AND coll = 'person' AND doc->>'sourceKey' = %s ORDER BY id LIMIT 1", key)
                assert _ids(c, "SELECT node_id FROM graph.person_by_source_key(%s)", key) == old, (org, key)
        as_role(c, "pipeshub_connectors", A)
        assert _ids(c, "SELECT node_id FROM graph.person_by_source_key('c1:u1')") == ["p0"]
        assert _ids(c, "SELECT node_id FROM graph.person_by_source_key('c1:only-a')") == ["pa"]
        as_role(c, "pipeshub_connectors", B)
        assert _ids(c, "SELECT node_id FROM graph.person_by_source_key('c1:only-a')") == []


def test_principals_by_email_return_the_users_groups_and_persons_of_the_org_whose_lowered_email_is_asked(rdb):
    cluster, name = rdb
    plain = """SELECT coll, id FROM graph.nodes WHERE org_id = current_setting('app.org_id') AND coll = ANY(%s::text[])
               AND lower(doc->>'email') IN (SELECT lower(x) FROM unnest(%s::text[]) x) ORDER BY coll, id"""
    with connect(cluster, name) as c:
        _seed_kg(c)
        for org in (A, B):
            as_role(c, "pipeshub_connectors", org)
            for colls in (["users"], ["groups"], ["person"], ["users", "groups", "person"], ["person", "users"], []):
                for emails in (["ann@x.com"], ["ANN@X.COM", "team@x.com"], ["onlya@x.com"], ["nope"], ["joe@x.com", "stone@x.com", "none@x.com"]):
                    new = sorted(_ids(c, "SELECT coll, node_id FROM graph.principals_by_email(%s::text[], %s::text[])", colls, emails))
                    assert new == _ids(c, plain, colls, emails), (org, colls, emails)
        as_role(c, "pipeshub_connectors", A)
        assert sorted(_ids(c, "SELECT coll, node_id FROM graph.principals_by_email('{users,groups,person}', '{ANN@x.com}')")) == [("groups", "g2"), ("person", "p0"), ("person", "p1"), ("users", "u1")]
        as_role(c, "pipeshub_connectors", B)
        assert _ids(c, "SELECT node_id FROM graph.principals_by_email('{users,groups,person}', '{onlya@x.com}')") == []


# The statement of find_people before the vetted read (blank-collapsed, lower-cased name given).
OLD_PEOPLE = """WITH u AS (
  SELECT x.id, array_to_string(array_remove(string_to_array(lower(coalesce(x.doc->>'fullName', '')), ' '), ''), ' ') AS name,
         array_to_string(array_remove(string_to_array(lower(coalesce(x.doc->>'firstName', '') || ' ' || coalesce(x.doc->>'lastName', '')), ' '), ''), ' ') AS parts
  FROM graph.nodes x WHERE x.org_id = current_setting('app.org_id') AND x.coll = 'person'),
hits AS (SELECT id, name, (name = %(w)s OR parts = %(w)s) AS exact FROM u
  WHERE name = %(w)s OR parts = %(w)s OR (length(%(w)s) >= 3 AND (starts_with(name, %(w)s) OR EXISTS (SELECT 1 FROM unnest(string_to_array(name, ' ')) w WHERE starts_with(w, %(w)s)))))
SELECT id FROM hits WHERE exact OR NOT EXISTS (SELECT 1 FROM hits WHERE exact) ORDER BY name COLLATE "C", id COLLATE "C" LIMIT %(n)s"""


def test_people_by_name_return_what_the_plain_statement_returned(rdb):
    cluster, name = rdb
    with connect(cluster, name) as c:
        _seed_kg(c)
        for org in (A, B):
            as_role(c, "pipeshub_connectors", org)
            for w in ("ann", "ann lee", "lee ann", "lee", "an", "stone", "anna stone", "mary", "ann smith", "100%", "100% joe_smith\\", "%", "_", "\\", "joe", "o_e", "only a", "nope", "smith"):
                for n in (1, 3, 1000):
                    assert _ids(c, "SELECT node_id FROM graph.people_by_name(%s, %s)", w, n) == _ids(c, OLD_PEOPLE, {"w": w, "n": n}), (org, w, n)
        as_role(c, "pipeshub_connectors", A)
        assert _ids(c, "SELECT node_id FROM graph.people_by_name('ann', 10)") == ["p7"]                              # an exact name wins over the beginnings
        assert _ids(c, "SELECT node_id FROM graph.people_by_name('ann lee', 10)") == ["p0", "p1"]
        assert _ids(c, "SELECT node_id FROM graph.people_by_name('ann', 10)") != _ids(c, "SELECT node_id FROM graph.people_by_name('anna', 10)")
        assert _ids(c, "SELECT node_id FROM graph.people_by_name('only a', 10)") == ["pa"]
        as_role(c, "pipeshub_connectors", B)
        assert _ids(c, "SELECT node_id FROM graph.people_by_name('only a', 10)") == []


KG_LOOKUPS = {
    "records_page_by_connector": ("(%s, %s, %s)", ("c1", "", 10)),
    "organizations_by_name": ("(%s, %s::text[])", (A, ["acme"])),
    "person_by_source_key": ("(%s)", ("c1:u1",)),
    "principals_by_email": ("(%s::text[], %s::text[])", (["users", "groups", "person"], ["ann@x.com"])),
    "people_by_name": ("(%s, %s)", ("ann", 10)),
}


def test_a_per_record_lookup_answers_nothing_without_an_org_or_for_another_one(rdb):
    cluster, name = rdb
    with connect(cluster, name) as c:
        _seed_kg(c)
        for org in ("", "c" * 24):
            as_role(c, "pipeshub_connectors", org)
            for fn, (marks, args) in KG_LOOKUPS.items():
                assert c.execute(f"SELECT count(*) FROM graph.{fn}{marks}", args).fetchone()[0] == 0, (org, fn)


BAD_KG = [
    ("records_page_by_connector", "(%s, %s, %s)", a) for a in ((None, "", 10), ("", "", 10), ("c" * 201, "", 10), ("c", None, 10), ("c", "x" * 201, 10), ("c", "", 0), ("c", "", 1001), ("c", "", None))] + [
    ("organizations_by_name", "(%s, %s::text[])", a) for a in ((None, []), ("", []), ("p" * 201, []), ("p", None), ("p", [None]), ("p", [""]), ("p", ["n" * 1001]), ("p", ["n"] * 1001))] + [
    ("person_by_source_key", "(%s)", a) for a in ((None,), ("",), ("k" * 401,))] + [
    ("principals_by_email", "(%s::text[], %s::text[])", a) for a in ((None, ["a"]), (["users"], None), ([None], ["a"]), (["other"], ["a"]), (["users"] * 4, ["a"]), (["users"], [None]),
                                                                  (["users"], [""]), (["users"], ["e" * 1001]), (["users"], ["e"] * 1001))] + [
    ("people_by_name", "(%s, %s)", a) for a in ((None, 10), ("", 10), ("n" * 401, 10), ("n", 0), ("n", 1001), ("n", None))]


@pytest.mark.parametrize(("fn", "marks", "args"), BAD_KG, ids=[f"{f}-{i}" for i, (f, _, _) in enumerate(BAD_KG)])
def test_a_per_record_lookup_refuses_a_bad_argument(rdb, fn, marks, args):
    cluster, name = rdb
    with connect(cluster, name) as c:
        as_role(c, "pipeshub_connectors", A)
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            c.execute(f"SELECT * FROM graph.{fn}{marks}", args)


def test_only_the_graph_services_may_call_a_per_record_lookup(rdb):
    cluster, name = rdb
    with connect(cluster, name) as c:
        for role, ok in (("pipeshub_connectors", True), ("pipeshub_indexing", True), ("pipeshub_query", True), ("pipeshub_node", False), ("pipeshub_jobs", False)):
            for fn in KG_LOOKUPS:
                has = c.execute("SELECT has_function_privilege(%s, p.oid, 'EXECUTE') FROM pg_proc p WHERE p.pronamespace = 'graph'::regnamespace AND p.proname = %s", (role, fn)).fetchone()[0]
                assert has is ok, (role, fn)


def test_the_per_record_lookups_use_their_index_where_the_plain_statement_cannot(rdb):
    """At 20,000 documents of each kind in the org (and 20,000 more in another): the function reaches the partial index of its key (no sequential scan of
    the org), the plain statement the provider sent before reaches the org only. Seven calls: the plpgsql plan cache goes generic after five."""
    cluster, name = rdb
    with connect(cluster, name) as c:
        for org in (A, B):
            as_role(c, "pipeshub_connectors", org)
            c.execute("INSERT INTO graph.nodes SELECT %s, 'records', 'r' || lpad(i::text, 6, '0'), jsonb_build_object('orgId', %s::text, 'connectorId', 'c' || (i %% 4)) FROM generate_series(1, 20000) i", (org, org))
            c.execute("INSERT INTO graph.nodes SELECT %s, 'organizations', 'o' || i, jsonb_build_object('normalizedName', 'n' || i, 'parentOrgId', %s::text, 'isExternal', true) FROM generate_series(1, 20000) i", (org, org))
            c.execute("INSERT INTO graph.nodes SELECT %s, 'person', 'p' || i, jsonb_build_object('sourceKey', 'k:' || i, 'email', 'p' || i || '@Example.com', "
                      "'fullName', (ARRAY['Ann','Bob','Cy','Dee','Eve','Flo','Gus','Hal'])[1 + i %% 8] || ' ' || md5(i::text), 'firstName', 'F' || i, 'lastName', 'L' || i) FROM generate_series(1, 20000) i", (org,))
            c.execute("INSERT INTO graph.nodes SELECT %s, 'groups', 'g' || i, jsonb_build_object('email', 'g' || i || '@Example.com') FROM generate_series(1, 20000) i", (org,))
            c.execute("INSERT INTO graph.nodes SELECT %s, 'users', 'u' || i, jsonb_build_object('email', 'u' || i || '@Example.com', 'userId', 'x' || i) FROM generate_series(1, 20000) i", (org,))
        c.execute("RESET ROLE")
        c.execute("ANALYZE graph.nodes")
        cases = [
            ("records_page_by_connector", "graph.records_page_by_connector(%s, %s, %s)", ("c1", "r010000", 100), "nodes_record_connector_c"),
            ("records_page_by_connector", "graph.records_page_by_connector(%s, %s, %s)", ("c1", "", 100), "nodes_record_connector_c"),
            ("organizations_by_name", "graph.organizations_by_name(%s, %s::text[])", (A, ["n777", "n9"]), "nodes_organization_name"),
            ("person_by_source_key", "graph.person_by_source_key(%s)", ("k:777",), "nodes_person_source_key"),
            ("principals_by_email", "graph.principals_by_email(%s::text[], %s::text[])", (["users"], ["U777@example.com"]), "org_id_lower_idx"),      # the baseline's index of nodes_user_email has its generated name
            ("principals_by_email", "graph.principals_by_email(%s::text[], %s::text[])", (["groups"], ["G777@example.com"]), "nodes_group_email"),
            ("principals_by_email", "graph.principals_by_email(%s::text[], %s::text[])", (["person"], ["P777@example.com"]), "nodes_person_email"),
            ("principals_by_email", "graph.principals_by_email(%s::text[], %s::text[])", (["users", "groups", "person"], ["x@example.com"]), "nodes_group_email"),
            ("people_by_name", "graph.people_by_name(%s, %s)", ("ann " + __import__("hashlib").md5(b"8").hexdigest(), 10), "nodes_person_name"),
            ("people_by_name", "graph.people_by_name(%s, %s)", ("f777 l777", 10), "nodes_person_parts"),
            ("people_by_name", "graph.people_by_name(%s, %s)", ("zzzzq", 10), "nodes_person_name_trgm"),
        ]
        for fn, call, params, index in cases:
            plans = vetted_harness.plans_auto_explain(c, "pipeshub_connectors", A, f"SELECT node_id FROM {call}", params)
            for i in (0, 6):
                assert _uses(plans[i], index), (fn, params, i, plans[i])
                assert "Seq Scan on" not in plans[i], (fn, params, i, plans[i])
                if fn == "records_page_by_connector":      # the index gives the order: a sort would read the whole connector for each page
                    assert not re.search(r"\bSort\b", plans[i]), (params, i, plans[i])
        # the plain statements the provider sent before reach the org and nothing finer: the key is in no index condition
        plain = {
            "connectorId": ("records", "n.doc->>'connectorId' = 'c1' AND n.id COLLATE \"C\" > 'r010000' ORDER BY n.id COLLATE \"C\" LIMIT 100"),
            "normalizedName": ("organizations", "n.doc->>'normalizedName' = ANY('{n777,n9}'::text[])"),
            "sourceKey": ("person", "n.doc->>'sourceKey' = 'k:777'"),
            "email": ("groups", "lower(n.doc->>'email') = 'g777@example.com'"),
        }
        for key, (coll, pred) in plain.items():
            plans = vetted_harness.plans_auto_explain(c, "pipeshub_connectors", A, f"SELECT n.id FROM graph.nodes n WHERE n.org_id = current_setting('app.org_id') AND n.coll = '{coll}' AND {pred}")
            assert not any(re.search(rf"Index Cond: .*{key}", p) for p in plans), (key, plans[0])


# ---- the entity pipeline's lookups (graph_0007) -----------------------------------------------------------------------------------------
def _ne(i, org, kind="person", key=None, aliases=None, merged=None, doc_org=None):
    d = {"orgId": doc_org or org, "kind": kind, "normKey": key or f"k{i}", **({"normalizedAliases": aliases} if aliases is not None else {}), **({"mergedInto": merged} if merged else {})}
    return (org, "namedEntities", f"ne{i}", json.dumps(d))


def _seed_entities(c):
    for org in (A, B):
        as_role(c, "pipeshub_connectors", org)
        for r in (_ne(1, org, key="anna lee", aliases=["a lee", "annie"]), _ne(2, org, key="anna lee smith"), _ne(3, org, key="anna leeds"), _ne(4, org, key="anna lee x", aliases=["annie"], merged="ne1"),
                  _ne(5, org, kind="org", key="anna lee", aliases=["annie"]), _ne(6, org, key="zed", aliases=["annie", "a lee"]), _ne(7, org, key="annie"),
                  _ne(8, org, key="anna lee y", doc_org="other"), _ne(9, org, key="Anna Lee", aliases=[]), _ne(10, org, key="anna%lee z"), _ne(11, org, key="b c", aliases=["annie"])):
            c.execute(NODE, r)
        for rec, vid, doc_org in (("r1", "v1", org), ("r1", "v2", org), ("r2", "v3", org), ("r1", "v4", "other"), ("r3", "v5", org)):
            c.execute(NODE, (org, "valueMentions", f"{rec}:{vid}", json.dumps({"recordId": rec, "orgId": doc_org, "kind": "date"})))
        c.execute(NODE, (org, "records", "r1", json.dumps({"recordId": "r1"})))                                                  # not a value mention
        for i, (vrid, rev) in enumerate((("v1", "1"), ("v1", "1"), ("v1", "2"), ("v2", "1"))):
            c.execute(NODE, (org, "stageStates", f"s{i}", json.dumps({"virtualRecordId": vrid, "rev": rev, "stage": "x"})))
        c.execute(NODE, (org, "records", "rs", json.dumps({"virtualRecordId": "v1", "rev": "1"})))                               # not a stage state


def test_the_entity_lookups_return_what_the_plain_statements_returned(rdb):
    cluster, name = rdb
    by_keys = """SELECT n.id FROM graph.nodes n WHERE n.org_id = current_setting('app.org_id') AND n.coll = 'namedEntities' AND n.doc->>'orgId' = %s AND n.doc->>'kind' = %s
                 AND (n.doc->>'normKey' = ANY(%s::text[]) OR n.doc->'normalizedAliases' ?| %s::text[]) ORDER BY n.id"""
    alias = """SELECT p.key, n.id FROM unnest(%s::text[], %s::text[]) AS p(kind, key) CROSS JOIN LATERAL (SELECT x.id FROM graph.nodes x WHERE x.org_id = current_setting('app.org_id')
               AND x.coll = 'namedEntities' AND x.doc->>'orgId' = %s AND x.doc->>'kind' = p.kind AND x.doc->'normalizedAliases' ? p.key AND x.doc->>'normKey' <> p.key
               AND x.doc->>'mergedInto' IS NULL ORDER BY x.id LIMIT %s) n ORDER BY p.key, n.id"""
    prefix = """SELECT p.key, n.id FROM unnest(%s::text[], %s::text[]) AS p(kind, key) CROSS JOIN LATERAL (SELECT x.id, x.doc FROM graph.nodes x WHERE x.org_id = current_setting('app.org_id')
                AND x.coll = 'namedEntities' AND x.doc->>'orgId' = %s AND x.doc->>'kind' = p.kind AND starts_with(x.doc->>'normKey', p.key || ' ')
                ORDER BY x.doc->>'normKey', x.id LIMIT %s) n ORDER BY p.key, n.doc->>'normKey', n.id"""
    with connect(cluster, name) as c:
        _seed_entities(c)
        for org in (A, B):
            as_role(c, "pipeshub_connectors", org)
            for owner in (org, A, B, "other"):
                for kind in ("person", "org", "none"):
                    for keys in (["anna lee"], ["annie"], ["a lee", "zed"], ["nope"], ["anna lee", "annie", "b c"], ["Anna Lee"]):
                        assert _ids(c, "SELECT node_id FROM graph.named_entities_by_keys(%s, %s, %s::text[])", owner, kind, keys) == _ids(c, by_keys, owner, kind, keys, keys), (org, owner, kind, keys)
                kinds, keys = ["person", "person", "org", "person"], ["annie", "a lee", "annie", "nope"]
                for n in (1, 2, 3, 1000):
                    assert _ids(c, "SELECT key, node_id FROM graph.named_entities_by_alias(%s, %s::text[], %s::text[], %s)", owner, kinds, keys, n) == _ids(c, alias, kinds, keys, owner, n), (org, owner, n)
                pkinds, pkeys = ["person", "person", "org", "person", "person"], ["anna lee", "anna", "anna lee", "nope", "anna%lee"]
                for n in (1, 2, 21):
                    assert _ids(c, "SELECT key, node_id FROM graph.named_entities_by_prefix(%s, %s::text[], %s::text[], %s)", owner, pkinds, pkeys, n) == _ids(c, prefix, pkinds, pkeys, owner, n), (org, owner, n)
        as_role(c, "pipeshub_connectors", A)
        assert _ids(c, "SELECT node_id FROM graph.named_entities_by_keys(%s, 'person', '{annie}')", A) == ["ne1", "ne11", "ne4", "ne6", "ne7"]
        assert _ids(c, "SELECT key, node_id FROM graph.named_entities_by_alias(%s, '{person}', '{annie}', 3)", A) == [("annie", "ne1"), ("annie", "ne11"), ("annie", "ne6")]
        assert _ids(c, "SELECT node_id FROM graph.named_entities_by_prefix(%s, '{person}', '{anna lee}', 21)", A) == ["ne2", "ne4"]


def test_value_mentions_and_stage_states_return_the_rows_of_the_org_that_hold_the_key(rdb):
    cluster, name = rdb
    with connect(cluster, name) as c:
        _seed_entities(c)
        for org in (A, B):
            as_role(c, "pipeshub_connectors", org)
            for ids in (["r1"], ["r1", "r2"], ["r3", "nope"], [], ["r1", "r1"]):
                old = _ids(c, "SELECT id FROM graph.nodes WHERE org_id = current_setting('app.org_id') AND coll = 'valueMentions' AND doc->>'recordId' = ANY(%s::text[]) ORDER BY id", ids)
                assert _ids(c, "SELECT node_id FROM graph.value_mentions_by_records(%s::text[]) ORDER BY node_id", ids) == old, (org, ids)
            for vrid, rev in (("v1", "1"), ("v1", "2"), ("v2", "2"), ("nope", "1")):
                old = _ids(c, "SELECT id FROM graph.nodes WHERE org_id = current_setting('app.org_id') AND coll = 'stageStates' AND doc->>'virtualRecordId' = %s AND doc->>'rev' = %s ORDER BY id", vrid, rev)
                assert sorted(_ids(c, "SELECT node_id FROM graph.stage_states_by_revision(%s, %s)", vrid, rev)) == old, (org, vrid, rev)
        as_role(c, "pipeshub_connectors", A)
        assert _ids(c, "SELECT node_id FROM graph.value_mentions_by_records('{r1,r2}') ORDER BY node_id") == ["r1:v1", "r1:v2", "r1:v4", "r2:v3"]
        assert sorted(_ids(c, "SELECT node_id FROM graph.stage_states_by_revision('v1', '1')")) == ["s0", "s1"]
        c.execute("INSERT INTO graph.nodes SELECT %s, 'valueMentions', 'big' || i, '{\"recordId\": \"big\"}' FROM generate_series(1, 100001) i", (A,))
        assert _ids(c, "SELECT count(*) FROM graph.value_mentions_by_records('{r1}')") == [3]
        assert _ids(c, "SELECT count(*) FROM graph.value_mentions_by_records('{big}')") == [100001]          # the bound: a caller that gets that many reads with its own statement
        assert _ids(c, "SELECT count(*) FROM graph.value_mentions_by_records('{big,r1}')") == [100001]


def test_an_entity_lookup_answers_nothing_without_an_org_or_for_another_one(rdb):
    cluster, name = rdb
    calls = {"named_entities_by_keys": ("(%s, 'person', '{annie}')", (A,)), "named_entities_by_alias": ("(%s, '{person}', '{annie}', 3)", (A,)),
             "named_entities_by_prefix": ("(%s, '{person}', '{anna lee}', 21)", (A,)), "value_mentions_by_records": ("('{r1}')", ()), "stage_states_by_revision": ("('v1', '1')", ())}
    with connect(cluster, name) as c:
        _seed_entities(c)
        for org in ("", "c" * 24):
            as_role(c, "pipeshub_connectors", org)
            for fn, (marks, args) in calls.items():
                assert c.execute(f"SELECT count(*) FROM graph.{fn}{marks}", args).fetchone()[0] == 0, (org, fn)
        for role, ok in (("pipeshub_connectors", True), ("pipeshub_indexing", True), ("pipeshub_query", True), ("pipeshub_node", False), ("pipeshub_jobs", False)):
            for fn in calls:
                has = c.execute("SELECT has_function_privilege(%s, p.oid, 'EXECUTE') FROM pg_proc p WHERE p.pronamespace = 'graph'::regnamespace AND p.proname = %s", (role, fn)).fetchone()[0]
                assert has is ok, (role, fn)


BAD_ENTITY = [
    ("named_entities_by_keys", "(%s, %s, %s::text[])", a) for a in ((None, "k", ["a"]), ("", "k", ["a"]), ("o", None, ["a"]), ("o", "", ["a"]), ("o", "k" * 201, ["a"]), ("o", "k", None), ("o", "k", [None]),
                                                                  ("o", "k", [""]), ("o", "k", ["a" * 1001]), ("o", "k", ["a"] * 1001))] + [
    (fn, "(%s, %s::text[], %s::text[], %s)", a) for fn in ("named_entities_by_alias", "named_entities_by_prefix")
    for a in ((None, ["k"], ["a"], 3), ("o", None, ["a"], 3), ("o", ["k"], None, 3), ("o", ["k"], ["a", "b"], 3), ("o", [None], ["a"], 3), ("o", [""], ["a"], 3), ("o", ["k"], [""], 3),
              ("o", ["k"], ["a"], 0), ("o", ["k"], ["a"], 1001), ("o", ["k"], ["a"], None))] + [
    ("value_mentions_by_records", "(%s::text[])", a) for a in ((None,), ([None],), ([""],), (["r" * 201],), (["r"] * 5001,))] + [
    ("stage_states_by_revision", "(%s, %s)", a) for a in ((None, "1"), ("", "1"), ("v" * 201, "1"), ("v", None), ("v", ""), ("v", "1" * 201))]


@pytest.mark.parametrize(("fn", "marks", "args"), BAD_ENTITY, ids=[f"{f}-{i}" for i, (f, _, _) in enumerate(BAD_ENTITY)])
def test_an_entity_lookup_refuses_a_bad_argument(rdb, fn, marks, args):
    cluster, name = rdb
    with connect(cluster, name) as c:
        as_role(c, "pipeshub_connectors", A)
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            c.execute(f"SELECT * FROM graph.{fn}{marks}", args)


def test_the_entity_lookups_use_their_index_where_the_plain_statement_cannot(rdb):
    """20,000 documents of each kind in the org (and as many in another): the function reaches the index of its key, the plain statement the provider sent before
    reaches the org only. Seven calls: the plpgsql plan cache goes generic after five."""
    cluster, name = rdb
    with connect(cluster, name) as c:
        for org in (A, B):
            as_role(c, "pipeshub_connectors", org)
            c.execute("INSERT INTO graph.nodes SELECT %s, 'namedEntities', 'ne' || i, jsonb_build_object('orgId', %s::text, 'kind', (ARRAY['person','org','place'])[1 + i %% 3], "
                      "'normKey', 'name ' || i, 'normalizedAliases', jsonb_build_array('al ' || i, 'al ' || (i + 1))) FROM generate_series(1, 20000) i", (org, org))
            c.execute("INSERT INTO graph.nodes SELECT %s, 'valueMentions', 'v' || i, jsonb_build_object('orgId', %s::text, 'recordId', 'r' || (i %% 4000), 'kind', 'date') FROM generate_series(1, 20000) i", (org, org))
            c.execute("INSERT INTO graph.nodes SELECT %s, 'stageStates', 's' || i, jsonb_build_object('virtualRecordId', 'v' || (i %% 5000), 'rev', 'r' || (i %% 2)) FROM generate_series(1, 20000) i", (org,))
        c.execute("RESET ROLE")
        c.execute("ANALYZE graph.nodes")
        cases = [
            ("graph.named_entities_by_keys(%s, %s, %s::text[])", (A, "person", ["name 777", "name 9"]), "nodes_ne_kind_key"),
            ("graph.named_entities_by_keys(%s, %s, %s::text[])", (A, "person", ["al 777"]), "nodes_ne_aliases"),
            ("graph.named_entities_by_alias(%s, %s::text[], %s::text[], %s)", (A, ["person", "org"], ["al 777", "al 778"], 3), "nodes_ne_aliases"),
            ("graph.named_entities_by_prefix(%s, %s::text[], %s::text[], %s)", (A, ["person"], ["name 7"], 21), "nodes_ne_kind_key"),
            ("graph.value_mentions_by_records(%s::text[])", (["r7", "r9"],), "nodes_value_record"),
            ("graph.value_mentions_by_records(%s::text[])", ([f"r{i}" for i in range(50)],), "nodes_value_record"),
            ("graph.stage_states_by_revision(%s, %s)", ("v777", "r1"), "nodes_stage_revision"),
        ]
        for call, params, index in cases:
            plans = vetted_harness.plans_auto_explain(c, "pipeshub_connectors", A, f"SELECT * FROM {call}", params)
            for i in (0, 6):
                assert _uses(plans[i], index), (call, params, i, plans[i])
                assert "Seq Scan on" not in plans[i], (call, params, i, plans[i])
        # the plain statements the provider sent before reach the org and nothing finer: the key is in no index condition
        plain = {
            "recordId": ("valueMentions", "n.doc->>'recordId' = ANY('{r7,r9}'::text[])"),
            "virtualRecordId": ("stageStates", "n.doc->>'virtualRecordId' = 'v777' AND n.doc->>'rev' = 'r1'"),
        }
        for key, (coll, pred) in plain.items():
            plans = vetted_harness.plans_auto_explain(c, "pipeshub_connectors", A, f"SELECT n.id FROM graph.nodes n WHERE n.org_id = current_setting('app.org_id') AND n.coll = '{coll}' AND {pred}")
            assert not any(re.search(rf"Index Cond: .*{key}", p) for p in plans), (key, plans[0])


# ---- groups and roles by external id, the stage sweep's page (graph_0008) ---------------------------------------------------------------
def _seed_external(c):
    for org in (A, B):
        as_role(c, "pipeshub_connectors", org)
        for coll, key, prefix in (("groups", "externalGroupId", "g"), ("recordGroups", "externalGroupId", "rg"), ("roles", "externalRoleId", "ro")):
            for i, (conn, ext) in enumerate((("c1", "e1"), ("c1", "e2"), ("c2", "e1"), ("c1", "E1"))):
                c.execute(NODE, (org, coll, f"{prefix}{i}", json.dumps({"connectorId": conn, key: ext})))
            c.execute(NODE, (org, coll, f"{prefix}x", json.dumps({"connectorId": "c1"})))                       # no external id
        c.execute(NODE, (org, "records", "rec", json.dumps({"connectorId": "c1", "externalGroupId": "e1"})))        # another collection with the same keys
        for i, (conn, stage, status, vrid, rev) in enumerate((("c1", "indexing", "PENDING", "v1", "1"), ("c1", "indexing", "PENDING", "v2", "1"), ("c1", "indexing", "DONE", "v3", "1"),
                                                             ("c1", "entities", "PENDING", "v1", "1"), ("c2", "indexing", "PENDING", "v1", "1"), ("c1", "indexing", "PENDING", "v4", "2"))):
            c.execute(NODE, (org, "stageStates", f"st{i}", json.dumps({"connectorId": conn, "stage": stage, "status": status, "virtualRecordId": vrid, "rev": rev})))
        for i, (vrid, rev) in enumerate((("v1", "1"), ("v1", "2"), ("v2", "1"), ("v1", "1"))):
            c.execute(NODE, (org, "records", f"rv{i}", json.dumps({"virtualRecordId": vrid, "contentRev": rev})))
    as_role(c, "pipeshub_connectors", A)
    c.execute(NODE, (A, "groups", "only-a", json.dumps({"connectorId": "c1", "externalGroupId": "only-a"})))


def test_a_node_by_external_id_is_the_one_of_its_collection_and_org(rdb):
    cluster, name = rdb
    keys = {"groups": "externalGroupId", "recordGroups": "externalGroupId", "roles": "externalRoleId"}
    with connect(cluster, name) as c:
        _seed_external(c)
        for org in (A, B):
            as_role(c, "pipeshub_connectors", org)
            for coll, key in keys.items():
                for conn, ext in (("c1", "e1"), ("c1", "E1"), ("c2", "e1"), ("c2", "e2"), ("c1", "only-a"), ("nope", "e1")):
                    old = _ids(c, f"SELECT id FROM graph.nodes WHERE org_id = current_setting('app.org_id') AND coll = '{coll}' AND doc->>'{key}' = %s AND doc->>'connectorId' = %s ORDER BY id", ext, conn)
                    new = _ids(c, "SELECT node_id FROM graph.node_by_external(%s, %s, %s)", coll, conn, ext)
                    assert len(new) == min(1, len(old)) and set(new) <= set(old), (org, coll, conn, ext, new, old)
        as_role(c, "pipeshub_connectors", A)
        assert _ids(c, "SELECT node_id FROM graph.node_by_external('groups', 'c1', 'only-a')") == ["only-a"]
        assert _ids(c, "SELECT node_id FROM graph.node_by_external('roles', 'c2', 'e1')") == ["ro2"]
        assert _ids(c, "SELECT node_id FROM graph.node_by_external('recordGroups', 'c1', 'only-a')") == []         # only in the collection of groups
        as_role(c, "pipeshub_connectors", B)
        assert _ids(c, "SELECT node_id FROM graph.node_by_external('groups', 'c1', 'only-a')") == []


def test_the_stage_sweeps_page_of_states_and_their_records(rdb):
    cluster, name = rdb
    page = """SELECT s.id FROM graph.nodes s WHERE s.org_id = current_setting('app.org_id') AND s.coll = 'stageStates' AND s.doc->>'connectorId' = %s AND s.doc->>'stage' = %s
              AND s.doc->>'status' = %s AND (%s::text IS NULL OR s.id > %s) ORDER BY s.id LIMIT %s"""
    with connect(cluster, name) as c:
        _seed_external(c)
        for org in (A, B):
            as_role(c, "pipeshub_connectors", org)
            for conn, stage, status in (("c1", "indexing", "PENDING"), ("c1", "indexing", "DONE"), ("c2", "indexing", "PENDING"), ("c1", "nope", "PENDING")):
                for after in (None, "st0", "st1", "zzz"):
                    for n in (1, 2, 1000):
                        assert _ids(c, "SELECT node_id FROM graph.stage_states_page(%s, %s, %s, %s, %s)", conn, stage, status, after or "", n) == _ids(c, page, conn, stage, status, after, after, n), (org, conn, stage, status, after, n)
            for vrids, revs in ((["v1"], ["1"]), (["v1", "v1", "v2"], ["1", "2", "1"]), (["v1", "v4"], ["1", "2"]), ([], []), (["nope"], ["1"])):
                old = sorted(_ids(c, "SELECT n.id FROM graph.nodes n JOIN unnest(%s::text[], %s::text[]) p(v, r) ON n.doc->>'virtualRecordId' = p.v AND n.doc->>'contentRev' = p.r "
                                     "WHERE n.org_id = current_setting('app.org_id') AND n.coll = 'records'", vrids, revs))
                assert sorted(set(_ids(c, "SELECT node_id FROM graph.records_by_revision(%s::text[], %s::text[])", vrids, revs))) == sorted(set(old)), (org, vrids, revs)
        as_role(c, "pipeshub_connectors", A)
        assert _ids(c, "SELECT node_id FROM graph.stage_states_page('c1', 'indexing', 'PENDING', 'st0', 10)") == ["st1", "st5"]
        assert sorted(_ids(c, "SELECT node_id FROM graph.records_by_revision('{v1,v2}', '{1,1}')")) == ["rv0", "rv2", "rv3"]


def test_the_external_and_stage_lookups_answer_nothing_without_an_org_refuse_bad_arguments_and_are_for_the_graph_services(rdb):
    cluster, name = rdb
    calls = {"node_by_external": "('groups', 'c1', 'e1')", "stage_states_page": "('c1', 'indexing', 'PENDING', '', 10)", "records_by_revision": "('{v1}', '{1}')"}
    bad = [("node_by_external", "(%s, %s, %s)", a) for a in ((None, "c", "e"), ("other", "c", "e"), ("groups", None, "e"), ("groups", "", "e"), ("groups", "c" * 201, "e"), ("groups", "c", None), ("groups", "c", ""), ("groups", "c", "e" * 2001))] + [
        ("stage_states_page", "(%s, %s, %s, %s, %s)", a) for a in ((None, "s", "t", "", 1), ("c", "", "t", "", 1), ("c", "s", None, "", 1), ("c", "s", "t", None, 1), ("c", "s", "t", "x" * 201, 1), ("c", "s", "t", "", 0), ("c", "s", "t", "", 1001))] + [
        ("records_by_revision", "(%s::text[], %s::text[])", a) for a in ((None, ["1"]), (["v"], None), ([None], ["1"]), ([""], ["1"]), (["v"], []), (["v" * 201], ["1"]), (["v"] * 1001, ["1"] * 1001))]
    with connect(cluster, name) as c:
        _seed_external(c)
        for org in ("", "c" * 24):
            as_role(c, "pipeshub_connectors", org)
            for fn, args in calls.items():
                assert c.execute(f"SELECT count(*) FROM graph.{fn}{args}").fetchone()[0] == 0, (org, fn)
        as_role(c, "pipeshub_connectors", A)
        for fn, marks, args in bad:
            with pytest.raises(psycopg.errors.InvalidParameterValue):
                c.execute(f"SELECT * FROM graph.{fn}{marks}", args)
            c.execute("ROLLBACK")
        for role, ok in (("pipeshub_connectors", True), ("pipeshub_indexing", True), ("pipeshub_query", True), ("pipeshub_node", False), ("pipeshub_jobs", False)):
            for fn in calls:
                has = c.execute("SELECT has_function_privilege(%s, p.oid, 'EXECUTE') FROM pg_proc p WHERE p.pronamespace = 'graph'::regnamespace AND p.proname = %s", (role, fn)).fetchone()[0]
                assert has is ok, (role, fn)


def test_the_external_and_stage_lookups_use_their_index_where_the_plain_statement_cannot(rdb):
    """30,000 groups, record groups and stage states in the org (and as many in another). Seven calls: the plpgsql plan cache goes generic after five."""
    cluster, name = rdb
    with connect(cluster, name) as c:
        for org in (A, B):
            as_role(c, "pipeshub_connectors", org)
            for coll, key, prefix in (("groups", "externalGroupId", "g"), ("recordGroups", "externalGroupId", "rg"), ("roles", "externalRoleId", "ro")):
                c.execute(f"INSERT INTO graph.nodes SELECT %s, '{coll}', '{prefix}' || i, jsonb_build_object('connectorId', 'c' || (i %% 10), '{key}', 'e' || i) FROM generate_series(1, 30000) i", (org,))
            c.execute("INSERT INTO graph.nodes SELECT %s, 'stageStates', 's' || lpad(i::text, 6, '0'), jsonb_build_object('connectorId', 'c' || (i %% 4), 'stage', 'indexing', "
                      "'status', (ARRAY['PENDING','DONE','FAILED'])[1 + i %% 3], 'virtualRecordId', 'v' || i, 'rev', '1') FROM generate_series(1, 30000) i", (org,))
            c.execute("INSERT INTO graph.nodes SELECT %s, 'records', 'r' || i, jsonb_build_object('virtualRecordId', 'v' || i, 'contentRev', '1') FROM generate_series(1, 30000) i", (org,))
        c.execute("RESET ROLE")
        c.execute("ANALYZE graph.nodes")
        cases = [
            ("graph.node_by_external(%s, %s, %s)", ("groups", "c7", "e777"), "nodes_group_by_external"),
            ("graph.node_by_external(%s, %s, %s)", ("roles", "c7", "e777"), "nodes_role_by_external"),
            ("graph.node_by_external(%s, %s, %s)", ("recordGroups", "c7", "e777"), "externalGroupId"),                  # the baseline's index of this collection has a generated name
            ("graph.stage_states_page(%s, %s, %s, %s, %s)", ("c1", "indexing", "PENDING", "s010000", 100), "nodes_stage_page"),
            ("graph.stage_states_page(%s, %s, %s, %s, %s)", ("c1", "indexing", "PENDING", "", 100), "nodes_stage_page"),
            ("graph.records_by_revision(%s::text[], %s::text[])", (["v7", "v77", "v777"], ["1", "1", "1"]), "nodes_record_virtual_rev"),
        ]
        for call, params, index in cases:
            plans = vetted_harness.plans_auto_explain(c, "pipeshub_connectors", A, f"SELECT node_id FROM {call}", params)
            for i in (0, 6):
                assert re.search(index, plans[i]) and "Seq Scan on" not in plans[i], (call, params, i, plans[i])
                if "stage_states_page" in call:      # the index gives the order: a sort would read every state of the status for each page
                    assert not re.search(r"\bSort\b", plans[i]), (params, i, plans[i])
        # the plain statements the provider sent before: the org only
        for coll, key in (("groups", "externalGroupId"), ("roles", "externalRoleId"), ("recordGroups", "externalGroupId")):
            plans = vetted_harness.plans_auto_explain(c, "pipeshub_connectors", A, f"SELECT n.id FROM graph.nodes n WHERE n.org_id = current_setting('app.org_id') AND n.coll = '{coll}' "
                                                                                    f"AND n.doc->>'{key}' = 'e777' AND n.doc->>'connectorId' = 'c7' LIMIT 1")
            assert not any(re.search(rf"Index Cond: .*{key}", p) for p in plans), (coll, plans[0])


def _seed_record_groups(c):
    for org in (A, B):
        as_role(c, "pipeshub_connectors", org)
        for i, (conn, model, deleted) in enumerate((("c1", "RECORD_GROUP_LEVEL", False), ("c1", "RECORD_GROUP_LEVEL", True), ("c1", "OTHER", False), ("c1", None, False),
                                                    ("c2", "RECORD_GROUP_LEVEL", False), ("c1", "RECORD_GROUP_LEVEL", None))):
            doc = {"connectorId": conn, **({"permissionModel": model} if model else {}), **({} if deleted is None else {"isDeleted": deleted})}
            c.execute(NODE, (org, "recordGroups", f"rg{i}", json.dumps(doc)))
        c.execute(NODE, (org, "groups", "g0", json.dumps({"connectorId": "c1", "permissionModel": "RECORD_GROUP_LEVEL"})))        # another collection with the same keys
    as_role(c, "pipeshub_connectors", A)
    c.execute(NODE, (A, "recordGroups", "only-a", json.dumps({"connectorId": "c1", "permissionModel": "RECORD_GROUP_LEVEL", "isDeleted": False})))


def test_the_declared_record_groups_of_a_connector_are_what_the_plain_statement_read_for_the_org_alone(rdb):
    cluster, name = rdb
    plain = """SELECT id FROM graph.nodes WHERE org_id = current_setting('app.org_id') AND coll = 'recordGroups' AND doc->>'connectorId' = %s
               AND doc->>'permissionModel' = 'RECORD_GROUP_LEVEL' AND NOT coalesce((doc->>'isDeleted')::boolean, false) ORDER BY id"""
    with connect(cluster, name) as c:
        _seed_record_groups(c)
        for org in (A, B):
            as_role(c, "pipeshub_connectors", org)
            for conn in ("c1", "c2", "nope"):
                live = "SELECT node_id FROM graph.declared_record_groups(%s) WHERE NOT coalesce(is_deleted::boolean, false)"          # the caller casts the flag
                assert sorted(_ids(c, live, conn)) == _ids(c, plain, conn), (org, conn)
        as_role(c, "pipeshub_connectors", A)
        assert sorted(_ids(c, "SELECT node_id FROM graph.declared_record_groups('c1')")) == ["only-a", "rg0", "rg1", "rg5"]          # the deleted one too: the caller drops it
        as_role(c, "pipeshub_connectors", B)
        assert sorted(_ids(c, "SELECT node_id FROM graph.declared_record_groups('c1')")) == ["rg0", "rg1", "rg5"]


def test_the_declared_record_groups_answer_nothing_without_an_org_refuse_bad_arguments_and_are_for_the_graph_services(rdb):
    cluster, name = rdb
    with connect(cluster, name) as c:
        _seed_record_groups(c)
        for org in ("", "c" * 24):
            as_role(c, "pipeshub_connectors", org)
            assert c.execute("SELECT count(*) FROM graph.declared_record_groups('c1')").fetchone()[0] == 0, org
        as_role(c, "pipeshub_connectors", A)
        for arg in (None, "", "c" * 201):
            with pytest.raises(psycopg.errors.InvalidParameterValue):
                c.execute("SELECT * FROM graph.declared_record_groups(%s)", (arg,))
            c.execute("ROLLBACK")
        for role, ok in (("pipeshub_connectors", True), ("pipeshub_indexing", True), ("pipeshub_query", True), ("pipeshub_node", False), ("pipeshub_jobs", False)):
            has = c.execute("SELECT has_function_privilege(%s, p.oid, 'EXECUTE') FROM pg_proc p WHERE p.pronamespace = 'graph'::regnamespace AND p.proname = 'declared_record_groups'", (role,)).fetchone()[0]
            assert has is ok, role


def test_the_declared_record_groups_use_the_connectors_index_where_the_plain_statement_cannot(rdb):
    """30,000 record groups in the org (and as many in another). Seven calls: the plpgsql plan cache goes generic after five."""
    cluster, name = rdb
    with connect(cluster, name) as c:
        for org in (A, B):
            as_role(c, "pipeshub_connectors", org)
            c.execute("INSERT INTO graph.nodes SELECT %s, 'recordGroups', 'rg' || i, jsonb_build_object('connectorId', 'c' || (i %% 10), 'externalGroupId', 'e' || i, "
                      "'permissionModel', CASE WHEN i %% 20 = 0 THEN 'RECORD_GROUP_LEVEL' ELSE 'OTHER' END) FROM generate_series(1, 30000) i", (org,))
        c.execute("RESET ROLE")
        c.execute("ANALYZE graph.nodes")
        plans = vetted_harness.plans_auto_explain(c, "pipeshub_connectors", A, "SELECT node_id FROM graph.declared_record_groups(%s)", ("c0",))
        for i in (0, 6):
            assert re.search(r"Index Cond: .*connectorId", plans[i]) and "Seq Scan on" not in plans[i], (i, plans[i])
        plans = vetted_harness.plans_auto_explain(c, "pipeshub_connectors", A, "SELECT n.id FROM graph.nodes n WHERE n.org_id = current_setting('app.org_id') AND n.coll = 'recordGroups' "
                                                                              "AND n.doc->>'connectorId' = 'c0' AND n.doc->>'permissionModel' = 'RECORD_GROUP_LEVEL'")
        assert not any(re.search(r"Index Cond: .*connectorId", p) for p in plans), plans[0]


STALE_PLAIN = """SELECT id FROM graph.nodes WHERE org_id = current_setting('app.org_id') AND coll = 'stageStates' AND doc->>'status' = ANY(%s::text[])
                 AND doc->'updatedAtMs' < to_jsonb(%s::bigint) ORDER BY doc->'updatedAtMs' LIMIT %s"""
UNHELD_PLAIN = """WITH g AS (SELECT s.doc->>'virtualRecordId' AS v, s.doc->>'rev' AS rev, max(coalesce((s.doc->>'updatedAtMs')::numeric, 0)) AS newest FROM graph.nodes s
                    WHERE s.org_id = current_setting('app.org_id') AND s.coll = 'stageStates' AND s.doc->>'virtualRecordId' = ANY(%s::text[]) AND s.doc->>'rev' IS NOT NULL GROUP BY 1, 2)
                 SELECT g.v, g.rev FROM g WHERE g.newest < %s::bigint AND NOT EXISTS (SELECT 1 FROM graph.nodes r WHERE r.org_id = current_setting('app.org_id') AND r.coll = 'records'
                    AND r.doc->>'virtualRecordId' = g.v AND r.doc->>'contentRev' = g.rev) ORDER BY g.v, g.rev"""


UNHELD_NEW = """SELECT virtual_record_id, revision FROM graph.stage_states_unheld(%s::text[]) GROUP BY 1, 2 HAVING max(coalesce(updated_at_ms::numeric, 0)) < %s::bigint ORDER BY 1, 2"""      # the caller's grouping


def _seed_sweeps(c):
    for org in (A, B):
        as_role(c, "pipeshub_connectors", org)
        for i, (status, vrid, rev, at) in enumerate((("PENDING", "v1", "1", 100), ("PENDING", "v1", "2", 200), ("RUNNING", "v2", "1", 300), ("DONE", "v3", "1", 400), ("PENDING", "v4", "1", None),
                                                    ("RUNNING", "v5", "1", 150), ("FAILED", "v6", "1", 50), ("PENDING", "v1", "1", 120), ("PENDING", None, None, 90))):
            doc = {"status": status, **({"virtualRecordId": vrid} if vrid else {}), **({"rev": rev} if rev else {}), **({"updatedAtMs": at} if at is not None else {})}
            c.execute(NODE, (org, "stageStates", f"s{i}", json.dumps(doc)))
        for i, (vrid, rev) in enumerate((("v1", "2"), ("v2", "1"), ("v3", "9"))):          # v1 rev 2 and v2 rev 1 are held; v1 rev 1, v3 rev 1, v5, v6 are not
            c.execute(NODE, (org, "records", f"r{i}", json.dumps({"virtualRecordId": vrid, "contentRev": rev})))
    as_role(c, "pipeshub_connectors", A)
    c.execute(NODE, (A, "stageStates", "only-a", json.dumps({"status": "PENDING", "virtualRecordId": "v7", "rev": "1", "updatedAtMs": 10})))


def test_the_stale_stage_states_and_the_unheld_revisions_are_what_the_plain_statements_read_for_the_org_alone(rdb):
    cluster, name = rdb
    with connect(cluster, name) as c:
        _seed_sweeps(c)
        for org in (A, B):
            as_role(c, "pipeshub_connectors", org)
            for statuses in (["PENDING"], ["PENDING", "RUNNING"], ["PENDING", "PENDING", "DONE", "FAILED", "RUNNING"], ["nope"], []):
                for before in (0, 100, 151, 1000):
                    for limit in (1, 2, 1000):
                        assert _ids(c, "SELECT node_id FROM graph.stage_states_stale(%s::text[], %s::bigint, %s)", statuses, before, limit) == _ids(c, STALE_PLAIN, statuses, before, limit), (org, statuses, before, limit)
            for vrids in (["v1"], ["v1", "v1", "v2", "v3"], ["v1", "v3", "v4", "v5", "v6", "v7"], ["nope"], []):
                for before in (0, 100, 130, 1000):
                    assert _ids(c, UNHELD_NEW, vrids, before) == _ids(c, UNHELD_PLAIN, vrids, before), (org, vrids, before)
        as_role(c, "pipeshub_connectors", A)
        assert _ids(c, "SELECT node_id FROM graph.stage_states_stale('{PENDING}', 1000, 3)") == ["only-a", "s8", "s0"]
        assert _ids(c, UNHELD_NEW, ["v1", "v3", "v7"], 1000) == [("v1", "1"), ("v3", "1"), ("v7", "1")]
        as_role(c, "pipeshub_connectors", B)
        assert _ids(c, UNHELD_NEW, ["v7"], 1000) == []


def test_the_sweep_reads_answer_nothing_without_an_org_refuse_bad_arguments_and_are_for_the_graph_services(rdb):
    cluster, name = rdb
    calls = {"stage_states_stale": "('{PENDING}', 1000, 10)", "stage_states_unheld": "('{v1}')"}
    bad = [("stage_states_stale", "(%s::text[], %s::bigint, %s)", a) for a in ((None, 1, 1), ([None], 1, 1), ([""], 1, 1), (["s" * 101], 1, 1), (["s"] * 21, 1, 1), (["s"], None, 1), (["s"], 1, None), (["s"], 1, 0), (["s"], 1, 1001))] + [
        ("stage_states_unheld", "(%s::text[])", a) for a in ((None,), ([None],), ([""],), (["v" * 201],), (["v"] * 1001,))]
    with connect(cluster, name) as c:
        _seed_sweeps(c)
        for org in ("", "c" * 24):
            as_role(c, "pipeshub_connectors", org)
            for fn, args in calls.items():
                assert c.execute(f"SELECT count(*) FROM graph.{fn}{args}").fetchone()[0] == 0, (org, fn)
        as_role(c, "pipeshub_connectors", A)
        for fn, marks, args in bad:
            with pytest.raises(psycopg.errors.InvalidParameterValue):
                c.execute(f"SELECT * FROM graph.{fn}{marks}", args)
            c.execute("ROLLBACK")
        for role, ok in (("pipeshub_connectors", True), ("pipeshub_indexing", True), ("pipeshub_query", True), ("pipeshub_node", False), ("pipeshub_jobs", False)):
            for fn in calls:
                has = c.execute("SELECT has_function_privilege(%s, p.oid, 'EXECUTE') FROM pg_proc p WHERE p.pronamespace = 'graph'::regnamespace AND p.proname = %s", (role, fn)).fetchone()[0]
                assert has is ok, (role, fn)


def test_the_sweep_reads_use_their_indexes_where_the_plain_statements_cannot(rdb):
    """30,000 stage states and records in the org (and as many in another). Seven calls: the plpgsql plan cache goes generic after five."""
    cluster, name = rdb
    with connect(cluster, name) as c:
        for org in (A, B):
            as_role(c, "pipeshub_connectors", org)
            c.execute("INSERT INTO graph.nodes SELECT %s, 'stageStates', 's' || lpad(i::text, 6, '0'), jsonb_build_object('status', (ARRAY['PENDING','DONE','FAILED','RUNNING'])[1 + i %% 4], "
                      "'virtualRecordId', 'v' || i, 'rev', '1', 'updatedAtMs', 1000 + i) FROM generate_series(1, 30000) i", (org,))
            c.execute("INSERT INTO graph.nodes SELECT %s, 'records', 'r' || i, jsonb_build_object('virtualRecordId', 'v' || i, 'contentRev', '2') FROM generate_series(1, 30000) i", (org,))
        c.execute("RESET ROLE")
        c.execute("ANALYZE graph.nodes")
        plans = vetted_harness.plans_auto_explain(c, "pipeshub_connectors", A, "SELECT node_id FROM graph.stage_states_stale(%s::text[], %s::bigint, %s)", (["PENDING", "RUNNING"], 100000, 50))
        for i in (0, 6):
            assert re.search(r"Index Cond: .*status.*updatedAtMs", plans[i]) and "nodes_stage_status" in plans[i] and "Seq Scan on" not in plans[i], (i, plans[i])
            assert re.search(r"Limit[^\n]*\n\s*->\s+(Merge Append|Index Scan using \S*nodes_stage_status)", plans[i]), (i, plans[i])      # the index gives each status's order: a sort of every state of the status would read them all
        plans = vetted_harness.plans_auto_explain(c, "pipeshub_connectors", A, "SELECT virtual_record_id FROM graph.stage_states_unheld(%s::text[])", (["v7", "v77", "v777"],))
        for i in (0, 6):
            assert "nodes_stage_revision" in plans[i] and "nodes_record_virtual_rev" in plans[i] and "Seq Scan on" not in plans[i], (i, plans[i])
        # the plain statements the provider sent before: the org only
        for sql, params, key in ((STALE_PLAIN, (["PENDING", "RUNNING"], 100000, 50), "status"), (UNHELD_PLAIN, (["v7", "v77", "v777"], 100000), "virtualRecordId")):
            plans = vetted_harness.plans_auto_explain(c, "pipeshub_connectors", A, sql, params)
            assert not any(re.search(rf"Index Cond: .*{key}", p) for p in plans), (key, plans[0])


def test_the_hop_order_indexes_are_partial_byte_ordered_and_on_every_leaf(rdb):
    """graph_0011: a page of a code-graph hop reads its anchor's edges in the order of these two indexes, on the link edges only."""
    cluster, name = rdb
    with connect(cluster, name) as c:
        for idx, coll, lead in (("edges_by_neighbour_in", "to_coll", "to_id"), ("edges_by_neighbour_out", "from_coll", "from_id")):
            d = c.execute("SELECT indexdef FROM pg_indexes WHERE schemaname = 'graph' AND tablename = 'edges' AND indexname = %s", (idx,)).fetchone()[0]
            assert f'{coll}, {lead} COLLATE "C"' in d and "WHERE" in d and "nodeRelations" in d and "recordLinks" in d, d
            leaves = c.execute("SELECT count(*) FROM pg_partition_tree(%s::regclass) WHERE isleaf", (f"graph.{idx}",)).fetchone()[0]
            parts = c.execute("SELECT count(*) FROM pg_partition_tree('graph.edges'::regclass) WHERE isleaf").fetchone()[0]
            assert leaves == parts and leaves > 0, (idx, leaves, parts)
