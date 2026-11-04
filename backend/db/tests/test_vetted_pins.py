"""Pinned vetted writes: a vetted write function may DELETE or use ON CONFLICT only where the kernel registry pins the exact
(function, relation, operation), in a fixed shape; the table it pins has no other writer.

The functions are the ``kt.w_*`` entries of vetted_manifest.yaml on the fixture tables of test_vetted.py (the generated per-function
suite there runs foreign-org, no-org, validation, privileges and plan checks on each of them). This file holds:
  - a planted violation for every catalog rule 100-115 (the generated SQL of a real function with one change, rolled back after
    the catalog was read) and for the rules that still apply to a function with no pin (57, 88, 89);
  - the refusals of ``kernel.allow_vetted_write_op``;
  - the catalog read independently of the view: no direct write privilege on a pinned table, its partitions or its columns;
  - what the roles can and cannot do: the query role cannot write, another org cannot be targeted, arguments cannot inject,
    a call outside an org context refuses.
It needs a PostgreSQL cluster, not PgBouncer."""
from contextlib import contextmanager

import psycopg
import pytest
from psycopg import errors as pg

from db import bootstrap_roles, vetted_harness
from db.lib import vetted

from .conftest import EXTENSIONS, VETTED, _drop_roles, run_migrate
from .rls_fixture import ORG_A, ORG_B
from .test_vetted import DB_NAME, SCHEMA_SQL, install
from .test_vetted import FNS as _FNS

FNS = _FNS     # every manifest a migrated database holds (test_vetted lists them)
DB = "t_pins"
PINNED = {f.qualified: f for f in FNS.values() if f.pins}
SIG = {f.qualified: f.signature for f in FNS.values()}
WRITER = "pipeshub_connectors"
SERVICES = ("pipeshub_node", "pipeshub_connectors", "pipeshub_indexing", "pipeshub_query")


@pytest.fixture(scope="module")
def pdb(cluster):
    with cluster.admin() as admin:
        admin.execute(f"DROP DATABASE IF EXISTS {DB} WITH (FORCE)")
        _drop_roles(admin)
        admin.execute(f"CREATE DATABASE {DB}")
    with cluster.admin(DB) as admin:
        bootstrap_roles.bootstrap(admin, ["kt", "ku", *VETTED], extensions=EXTENSIONS)
    r = run_migrate(cluster.url(DB, "pipeshub_migrate"), PG_MIGRATE_SURFACE_CHECK="skip")
    assert r.returncode == 0, r.stdout
    with cluster.admin(DB) as admin:
        admin.execute(SCHEMA_SQL.replace(DB_NAME, DB))
        for fn in FNS.values():
            if fn.schema in ("kt", "ku"):
                install(admin, vetted.render(fn))
    yield cluster
    with cluster.admin() as admin:
        admin.execute(f"DROP DATABASE IF EXISTS {DB} WITH (FORCE)")
        _drop_roles(admin)


@contextmanager
def conn(cluster):
    with cluster.admin(DB) as c:
        yield c


def violations(c: psycopg.Connection, full: bool = False) -> list[tuple[str, str]]:
    """The pin checks alone (a second), or, with ``full``, the whole catalog view the migrate job reads (tens of seconds on a loaded host)."""
    views = ("kernel.rls_violations",) if full else ("kernel.pin_violations",)
    return [tuple(r) for v in views for r in c.execute(f"SELECT rule, object FROM {v} ORDER BY 1, 2")] + vetted.problems(c, FNS)


def planted(cluster, sql: str, full: bool = False) -> list[tuple[str, str]]:
    """The catalog after ``sql``, rolled back."""
    with conn(cluster) as c:
        c.execute("BEGIN")
        try:
            c.execute(sql)
            return violations(c, full)
        finally:
            c.execute("ROLLBACK")


def mutated(fn: str, old: str, new: str) -> str:
    sql = vetted.render(FNS[SIG[fn]], replace=True)
    assert sql.count(old) == 1, f"{old!r} is not exactly once in the generated SQL of {fn}"
    return sql.replace(old, new)


def installed(cluster, sql: str, full: bool = False) -> list[tuple[str, str]]:
    """Like ``planted``, for a generated function file (it needs the owner role and its own transaction)."""
    with conn(cluster) as c:
        c.execute("BEGIN")
        try:
            install(c, sql)
            return violations(c, full)
        finally:
            c.execute("ROLLBACK")


# ---- the fixture is clean ---------------------------------------------------------------------------------------------------

def test_the_pinned_fixture_leaves_the_catalog_clean(pdb):
    with conn(pdb) as c:
        assert violations(c, full=True) == []
        assert c.execute("SELECT count(*) FROM kernel.vetted_write_pins").fetchone()[0] == sum(len(f.pins) for f in PINNED.values())


def test_the_manifest_pins_are_what_the_registry_holds(pdb):
    with conn(pdb) as c:
        for name, f in PINNED.items():
            got = c.execute("SELECT rel::text, op, key_cols, set_cols, txn_col, max_items FROM kernel.vetted_write_pins WHERE proc = %s::regprocedure ORDER BY 1, 2",
                            (f.signature,)).fetchall()
            want = sorted((p.rel, p.op, list(p.key) if p.key is not None else None, list(p.set) if p.set is not None else None, p.txn, p.max_items) for p in f.pins)
            assert [tuple(g) for g in got] == want, name


# ---- planted violations -----------------------------------------------------------------------------------------------------

DEL = "DELETE FROM kt.grants t USING unnest(p_principals, p_nodes, p_gtypes) AS k(a, b, c) WHERE t.org_id = v_org AND t.principal_sk = k.a AND t.node_sk = k.b AND t.gtype = k.c;"
UPS = ("INSERT INTO kt.grants (org_id, principal_sk, node_sk, gtype, role) SELECT v_org, k.a, k.b, k.c, k.d FROM unnest(p_principals, p_nodes, p_gtypes, p_roles) AS k(a, b, c, d)\n"
       "      ON CONFLICT (org_id, principal_sk, node_sk, gtype) DO UPDATE SET role = excluded.role;")
GD, GS, FR, CA, KA, PP = "kt.w_grants_delete", "kt.w_grants_set", "kt.w_fence_remove", "kt.w_changes_ack", "kt.w_keys_add", "kt.w_pref_put"
SET_ROW = "SELECT kernel.allow_vetted_write_op"


def _fn(name: str) -> str:
    return SIG[name]


PLANTS = [
    # (name, SQL, rule prefix that must be reported)
    ("100 a pinned function is dropped", "DROP FUNCTION kt.w_fence_add(bigint, text)", "100"),
    ("101 the registry calls a pinned function a read function", f"UPDATE kernel.function_registry SET class = 'read', writer = NULL WHERE proc = '{_fn('kt.w_fence_add')}'::regprocedure", "101"),
    ("102 a pinned function is owned by another schema's vetted role", "ALTER FUNCTION kt.w_fence_add(bigint, text) OWNER TO pipeshub_vetted_ku", "102"),
    ("103 a pin on a table that did not opt into vetted_write",
     f"INSERT INTO kernel.vetted_write_pins (proc, rel, op) VALUES ('{_fn('kt.w_fence_add')}'::regprocedure, 'kt.secrets'::regclass, 'insert')", "103"),
    ("103 a pin on a partition",
     f"INSERT INTO kernel.vetted_write_pins (proc, rel, op) VALUES ('{_fn('kt.w_fence_add')}'::regprocedure, 'kt.grants_h0'::regclass, 'insert')", "103"),
    ("104 a delete pin that names part of the key",
     f"UPDATE kernel.vetted_write_pins SET key_cols = '{{principal_sk}}' WHERE proc = '{_fn(GD)}'::regprocedure", "104"),
    ("104 an upsert pin that sets a primary key column",
     f"UPDATE kernel.vetted_write_pins SET set_cols = '{{role,node_sk}}' WHERE proc = '{_fn(GS)}'::regprocedure", "104"),
    ("104 a transaction pin on a column that is not xid8",
     f"UPDATE kernel.vetted_write_pins SET txn_col = 'node_id' WHERE proc = '{_fn(CA)}'::regprocedure", "104"),
    ("105 a pinned function deletes from a table it does not pin",
     mutated(GD, "GET DIAGNOSTICS", "DELETE FROM kt.fences f WHERE f.org_id = v_org AND f.id = 1;\n    GET DIAGNOSTICS"), "105 pinned function runs delete on kt.fences"),
    ("105 a pinned function inserts into a table it does not pin",
     mutated(GD, "GET DIAGNOSTICS", "INSERT INTO kt.fences (org_id, id, reason) VALUES (v_org, 1, 'x');\n    GET DIAGNOSTICS"), "105 pinned function runs insert"),
    ("105 a pinned function also updates the table it only deletes from",
     mutated(GD, "GET DIAGNOSTICS", "UPDATE kt.grants SET role = 3 WHERE org_id = v_org;\n    GET DIAGNOSTICS"), "105 pinned function runs update"),
    ("105 a pinned function truncates", mutated(GD, "GET DIAGNOSTICS", "TRUNCATE kt.fences;\n    GET DIAGNOSTICS"), "105 pinned function truncates"),
    ("105 a pin for an operation the function never performs", f"{SET_ROW}('{_fn(GD)}', 'kt.grants', 'update')", "105 pin names an operation"),
    ("106 a delete without the org predicate", mutated(GD, "WHERE t.org_id = v_org AND t.principal_sk", "WHERE t.principal_sk"), "106"),
    ("106 a delete whose org predicate compares the row with itself", mutated(GD, "t.org_id = v_org", "t.org_id = t.org_id"), "106"),
    ("106 a delete with an OR", mutated(GD, "AND t.gtype = k.c;", "AND t.gtype = k.c OR true;"), "106"),
    ("106 a delete by part of the key", mutated(GD, " AND t.gtype = k.c;", ";"), "106"),
    ("106 a delete by range", mutated(GD, "t.node_sk = k.b", "t.node_sk > k.b"), "106"),
    ("106 a delete whose key columns are paired with the wrong array columns", mutated(GD, "t.principal_sk = k.a AND t.node_sk = k.b", "t.principal_sk = k.b AND t.node_sk = k.a"), "106"),
    ("106 a delete of the whole org", mutated(GD, "USING unnest(p_principals, p_nodes, p_gtypes) AS k(a, b, c) WHERE t.org_id = v_org AND t.principal_sk = k.a AND t.node_sk = k.b AND t.gtype = k.c;",
                                              "WHERE t.org_id = v_org;"), "106"),
    ("106 a delete by the current transaction with a comparison other than equality", mutated(CA, "t.txid = pg_current_xact_id()", "t.txid <= pg_current_xact_id()"), "106"),
    ("106 a delete by the current transaction and something else", mutated(CA, "t.txid = pg_current_xact_id();", "t.txid = pg_current_xact_id() AND t.coll = 1;"), "106"),
    ("106 a delete returning rows", mutated(FR, "t.id = p_id;", "t.id = p_id RETURNING t.reason;"), "106"),
    ("107 an upsert whose conflict target is not the primary key",
     mutated(GS, "ON CONFLICT (org_id, principal_sk, node_sk, gtype)", "ON CONFLICT (principal_sk, node_sk, gtype)"), "107"),
    ("107 an upsert that sets more than it pins", mutated(GS, "SET role = excluded.role;", "SET role = excluded.role, node_sk = excluded.node_sk;"), "107"),
    ("107 an upsert that sets a value other than the excluded one", mutated(GS, "SET role = excluded.role;", "SET role = 3;"), "107"),
    ("107 an upsert with a WHERE after the SET", mutated(GS, "SET role = excluded.role;", "SET role = excluded.role WHERE kt.grants.role <> 3;"), "107"),
    ("107 an upsert that writes an org other than the caller's", mutated(GS, "SELECT v_org, k.a", "SELECT k.a, k.a"), "107"),
    ("107 an upsert that selects from another table", mutated(GS, "SELECT v_org, k.a, k.b, k.c, k.d", "SELECT v_org, (SELECT 1), k.b, k.c, k.d"), "107"),
    ("107 an upsert that returns a non-key column", mutated(GS, "SET role = excluded.role;", "SET role = excluded.role RETURNING role;"), "107"),
    ("107 an upsert that does nothing where the pin updates", mutated(GS, "DO UPDATE SET role = excluded.role;", "DO NOTHING;"), "107"),
    ("107 an upsert that updates where the pin does nothing", mutated(KA, "DO NOTHING;", "DO UPDATE SET sk = excluded.sk;"), "107"),
    ("107 a scalar upsert whose first value is not the org", mutated(PP, "VALUES (v_org, p_k, p_v)", "VALUES (p_k, p_k, p_v)"), "107"),
    ("107 an upsert joined to another table", mutated(GS, "AS k(a, b, c, d)\n", "AS k(a, b, c, d) JOIN kt.fences f ON f.id = k.a\n"), "107"),
    ("108 an upsert whose array has no cardinality cap", mutated(GS, "cardinality(p_principals) > 5000", "p_principals IS NULL AND false"), "108"),
    ("108 a delete whose cap is above the pin's", mutated(GD, "cardinality(p_nodes) > 5000", "cardinality(p_nodes) > 6000"), "108"),
    ("108 a delete whose cap guard is inside an IF that never runs", mutated(GD, "IF cardinality(p_nodes) > 5000 THEN RAISE EXCEPTION 'w_grants_delete: p_nodes takes at most 5000 items' USING ERRCODE = '22023'; END IF;", "IF false THEN IF cardinality(p_nodes) > 5000 THEN RAISE EXCEPTION 'w_grants_delete: p_nodes takes at most 5000 items' USING ERRCODE = '22023'; END IF; END IF;"), "108"),
    ("108 a delete whose cap guard is in the ELSE of an IF", mutated(GD, "IF cardinality(p_nodes) > 5000 THEN RAISE EXCEPTION 'w_grants_delete: p_nodes takes at most 5000 items' USING ERRCODE = '22023'; END IF;", "IF true THEN NULL; ELSE IF cardinality(p_nodes) > 5000 THEN RAISE EXCEPTION 'w_grants_delete: p_nodes takes at most 5000 items' USING ERRCODE = '22023'; END IF; END IF;"), "108"),
    ("108 the cap guard comes after an early RETURN", mutated(GD, "IF cardinality(p_nodes) > 5000 THEN RAISE EXCEPTION 'w_grants_delete: p_nodes takes at most 5000 items' USING ERRCODE = '22023'; END IF;", "IF p_nodes IS NOT NULL THEN RETURN 0; END IF; IF cardinality(p_nodes) > 5000 THEN RAISE EXCEPTION 'w_grants_delete: p_nodes takes at most 5000 items' USING ERRCODE = '22023'; END IF;"), "108"),
    ("108 a delete that unnests something that is not an argument", mutated(GD, "USING unnest(p_principals, p_nodes, p_gtypes)", "USING unnest(p_principals, p_nodes, v_n)"), "108"),
    ("109 a scalar delete keyed by a variable", mutated(FR, "t.id = p_id;", "t.id = v_n;"), "109"),
    ("114 a function with no pin updates a pinned table",
     "CREATE FUNCTION kt.rogue(p_x text) RETURNS void LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, kt, pg_temp SET standard_conforming_strings = on "
     "AS $b$ BEGIN IF p_x IS NULL THEN RAISE EXCEPTION 'bad' USING ERRCODE = '22023'; END IF; "
     "UPDATE kt.prefs SET v = p_x WHERE org_id = current_setting('app.org_id', true); END $b$; "
     "ALTER FUNCTION kt.rogue(text) OWNER TO pipeshub_vetted_kt; REVOKE ALL ON FUNCTION kt.rogue(text) FROM PUBLIC; "
     "GRANT EXECUTE ON FUNCTION kt.rogue(text) TO pipeshub_node; SELECT kernel.register_function('kt.rogue(text)', 'write', 'pipeshub_node', 'org', false)", "114"),
    ("114 a function with no pin deletes from a pinned table",
     "CREATE FUNCTION kt.rogue(p_x text) RETURNS void LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, kt, pg_temp SET standard_conforming_strings = on "
     "AS $b$ BEGIN IF p_x IS NULL THEN RAISE EXCEPTION 'bad' USING ERRCODE = '22023'; END IF; "
     "DELETE FROM kt.grants WHERE org_id = current_setting('app.org_id', true); END $b$; "
     "ALTER FUNCTION kt.rogue(text) OWNER TO pipeshub_vetted_kt; REVOKE ALL ON FUNCTION kt.rogue(text) FROM PUBLIC; "
     "GRANT EXECUTE ON FUNCTION kt.rogue(text) TO pipeshub_node; SELECT kernel.register_function('kt.rogue(text)', 'write', 'pipeshub_node', 'org', false)", "114"),
    ("113 a pinned function is executable by the query role", "GRANT EXECUTE ON FUNCTION kt.w_fence_add(bigint, text) TO pipeshub_query", "113"),
    ("113 a pinned function is executable by PUBLIC", "GRANT EXECUTE ON FUNCTION kt.w_fence_add(bigint, text) TO PUBLIC", "113"),
    ("116 a pinned function changes the org in the middle of the call",
     mutated(GD, "GET DIAGNOSTICS", "PERFORM set_config('app.org_id', 'x', true);\n    GET DIAGNOSTICS"), "116"),
    ("116 a pinned function switches role", mutated(GD, "GET DIAGNOSTICS", "SET LOCAL ROLE pipeshub_owner;\n    GET DIAGNOSTICS"), "116"),
    ("116 a pinned function reads the settings a second time", mutated(GD, "GET DIAGNOSTICS", "PERFORM current_setting('app.org_id', true);\n    GET DIAGNOSTICS"), "116"),
    ("117 a plain insert whose org is an argument", mutated("kt.w_fence_add", "VALUES (v_org, p_id, p_reason)", "VALUES (p_reason, p_id, p_reason)"), "117"),
    ("117 a plain insert with no org column", mutated("kt.w_fence_add", "INSERT INTO kt.fences (org_id, id, reason) VALUES (v_org, p_id, p_reason)", "INSERT INTO kt.fences (id, reason) VALUES (p_id, p_reason)"), "117"),
    ("117 a plain update without the org predicate", mutated("kt.w_fence_reason", "WHERE t.org_id = v_org AND t.id = p_id", "WHERE t.id = p_id"), "117"),
    ("117 a plain update whose org predicate is not first", mutated("kt.w_fence_reason", "WHERE t.org_id = v_org AND t.id = p_id", "WHERE t.id = p_id AND t.org_id = v_org"), "117"),
    ("117 a plain update with an OR", mutated("kt.w_fence_reason", "AND t.id = p_id", "AND t.id = p_id OR t.reason = ''"), "117"),
    ("115 another schema's vetted role writes a pinned table", "GRANT DELETE ON kt.grants TO pipeshub_vetted_ku", "115"),
    # ---- the rules that apply to a function with no pin are still on
    ("57 an unpinned write function uses ON CONFLICT",
     "CREATE FUNCTION kt.rogue(p_x text) RETURNS void LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, kt, pg_temp SET standard_conforming_strings = on "
     "AS $b$ BEGIN IF p_x IS NULL THEN RAISE EXCEPTION 'bad' USING ERRCODE = '22023'; END IF; "
     "INSERT INTO kt.notes (org_id, id, title) VALUES (current_setting('app.org_id', true), p_x, 't') ON CONFLICT DO NOTHING; END $b$; "
     "ALTER FUNCTION kt.rogue(text) OWNER TO pipeshub_vetted_kt; REVOKE ALL ON FUNCTION kt.rogue(text) FROM PUBLIC; "
     "GRANT EXECUTE ON FUNCTION kt.rogue(text) TO pipeshub_node; SELECT kernel.register_function('kt.rogue(text)', 'write', 'pipeshub_node', 'org', false)", "57"),
    ("88 an unpinned write function deletes",
     "CREATE FUNCTION kt.rogue(p_x text) RETURNS void LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, kt, pg_temp SET standard_conforming_strings = on "
     "AS $b$ BEGIN IF p_x IS NULL THEN RAISE EXCEPTION 'bad' USING ERRCODE = '22023'; END IF; "
     "DELETE FROM kt.notes WHERE org_id = current_setting('app.org_id', true); END $b$; "
     "ALTER FUNCTION kt.rogue(text) OWNER TO pipeshub_vetted_kt; REVOKE ALL ON FUNCTION kt.rogue(text) FROM PUBLIC; "
     "GRANT EXECUTE ON FUNCTION kt.rogue(text) TO pipeshub_node; SELECT kernel.register_function('kt.rogue(text)', 'write', 'pipeshub_node', 'org', false)", "88"),
    ("88 a pinned function deletes where it pins only an upsert",
     mutated(GS, "GET DIAGNOSTICS", "DELETE FROM kt.grants t WHERE t.org_id = v_org;\n    GET DIAGNOSTICS"), "105 pinned function runs delete"),
    ("89 a vetted role's DELETE privilege on a table with no delete pin", "GRANT DELETE ON kt.notes TO pipeshub_vetted_kt", "89"),
]


@pytest.mark.parametrize("plant", PLANTS, ids=[p[0] for p in PLANTS])
def test_a_planted_violation_is_reported(pdb, plant):
    _, sql, rule = plant
    full = rule in ("57", "88", "89")             # the rules of the old views, which the public view must still apply to a function or table with no pin
    got = installed(pdb, sql, full) if sql.startswith("-- generated") else planted(pdb, sql, full)
    assert any(r.startswith(rule) for r, _ in got), got


def test_a_delete_with_its_conditions_in_another_order_is_accepted_when_the_pairs_are_right(pdb):
    # the pairing is what matters (key column i with alias column i), not the order the conditions are written in
    reordered = mutated(GD, "t.principal_sk = k.a AND t.node_sk = k.b AND t.gtype = k.c", "t.gtype = k.c AND t.principal_sk = k.a AND t.node_sk = k.b")
    assert not any(r.startswith("106") for r, _ in planted(pdb, reordered, False))


FOLDED = ("106 a delete without the org predicate", "107 an upsert that sets more than it pins", "114 a function with no pin updates a pinned table",
          "113 a pinned function is executable by PUBLIC")


@pytest.mark.parametrize("plant", [p for p in PLANTS if p[0] in FOLDED], ids=lambda p: p[0])
def test_the_pin_checks_are_part_of_the_catalog_view_the_migration_reads(pdb, plant):
    _, sql, rule = plant
    got = installed(pdb, sql, True) if sql.startswith("-- generated") else planted(pdb, sql, True)
    assert any(r.startswith(rule) for r, _ in got), got


# ---- direct privileges on a pinned table: every route -----------------------------------------------------------------------

PRIV_PLANTS = [
    ("the query role is granted UPDATE on an access table", "GRANT UPDATE ON kt.grants TO pipeshub_query", "UPDATE"),
    ("the node role is granted INSERT", "GRANT INSERT ON kt.grants TO pipeshub_node", "INSERT"),
    ("the connectors role is granted DELETE on a table whose delete is pinned", "GRANT DELETE ON kt.fences TO pipeshub_connectors", "DELETE"),
    ("the connectors role is granted DELETE on the txn-pinned table", "GRANT DELETE ON kt.changes TO pipeshub_connectors", "DELETE"),
    ("the jobs role is granted INSERT", "GRANT INSERT ON kt.keys TO pipeshub_jobs", "INSERT"),
    ("the app group is granted UPDATE", "GRANT UPDATE ON kt.prefs TO pipeshub_app", "UPDATE"),
    ("PUBLIC is granted INSERT", "GRANT INSERT ON kt.prefs TO PUBLIC", "INSERT"),
    ("a column grant: UPDATE (role)", "GRANT UPDATE (role) ON kt.grants TO pipeshub_connectors", "UPDATE"),
    ("a column grant: INSERT (org_id)", "GRANT INSERT (org_id) ON kt.fences TO pipeshub_indexing", "INSERT"),
    ("a partition leaf is granted INSERT", "GRANT INSERT ON kt.grants_h0 TO pipeshub_query", "INSERT"),
    ("a role the login can reach is granted UPDATE",
     "CREATE ROLE helper_r NOLOGIN; GRANT helper_r TO pipeshub_connectors; GRANT UPDATE ON kt.grants TO helper_r", "UPDATE"),
]


@pytest.mark.parametrize("plant", PRIV_PLANTS, ids=[p[0] for p in PRIV_PLANTS])
def test_a_direct_write_privilege_on_a_pinned_table_is_reported(pdb, plant):
    _, sql, priv = plant
    got = planted(pdb, sql, full=priv in ("UPDATE", "DELETE") and sql.startswith("GRANT UPDATE ON kt.grants TO pipeshub_query"))
    assert any(r.startswith("110 ") and f"holds {priv}" in r for r, _ in got), got


def test_a_privilege_for_an_operation_nobody_pinned_is_not_a_violation(pdb):
    """kt.changes is pinned for delete only: the trigger's INSERT by the writers stays; kt.fences has all three pinned."""
    assert planted(pdb, "GRANT INSERT ON kt.changes TO pipeshub_node") == []
    assert planted(pdb, "GRANT SELECT ON kt.grants, kt.fences, kt.keys, kt.prefs TO pipeshub_node, pipeshub_jobs, pipeshub_app") == []


def test_no_role_but_the_vetted_owner_holds_a_write_privilege_on_a_pinned_table_read_from_the_catalog(pdb):
    """The same fact as check 110, read straight from pg_class and pg_attribute (table, every partition, every column)."""
    with conn(pdb) as c:
        want = {"insert": {"INSERT"}, "upsert": {"INSERT", "UPDATE"}, "update": {"UPDATE"}, "delete": {"DELETE"}}
        locked: dict[str, set[str]] = {}
        for rel, op in c.execute("SELECT DISTINCT rel::text, op FROM kernel.vetted_write_pins WHERE rel::text LIKE 'kt.%'"):
            locked.setdefault(rel, set()).update(want[op])
        assert set(locked) == {"kt.grants", "kt.fences", "kt.keys", "kt.prefs", "kt.changes"}
        for rel, privs in locked.items():
            leaves = [r[0] for r in c.execute("SELECT relid::text FROM pg_partition_tree(%s::regclass) UNION SELECT %s::text", (rel, rel))]
            for leaf in leaves:
                rows = c.execute("SELECT a.grantee::regrole::text, a.privilege_type FROM pg_class c, aclexplode(c.relacl) a WHERE c.oid = %s::regclass "
                                 "UNION ALL SELECT a.grantee::regrole::text, a.privilege_type FROM pg_attribute t, aclexplode(t.attacl) a WHERE t.attrelid = %s::regclass AND t.attacl IS NOT NULL",
                                 (leaf, leaf)).fetchall()
                bad = [(g, p) for g, p in rows if p in privs and g not in ("pipeshub_owner", "pipeshub_vetted_kt", "pipeshub_restore")]
                assert bad == [], (leaf, bad)
        # the owner of the schema's functions is the only role with the privilege the functions use; DELETE only where it is pinned
        dels = {r[0] for r in c.execute("SELECT c.oid::regclass::text FROM pg_class c, aclexplode(c.relacl) a WHERE a.grantee = 'pipeshub_vetted_kt'::regrole "
                                        "AND a.privilege_type = 'DELETE' AND c.relnamespace = 'kt'::regnamespace")}
        assert dels == {"kt.grants", "kt.fences", "kt.changes"}


# ---- allow_vetted_write_op refuses what it should ------------------------------------------------------------------------------

REFUSED = [
    ("a read function", "SELECT kernel.allow_vetted_write_op('kt.notes_by_title(text, integer)', 'kt.notes', 'update')"),
    ("a global table", "SELECT kernel.allow_vetted_write_op('kt.w_fence_add(bigint, text)', 'kt.tokens', 'insert')"),
    ("a table that did not opt into vetted_write", "SELECT kernel.allow_vetted_write_op('kt.w_fence_add(bigint, text)', 'kt.secrets', 'insert')"),
    ("a partition", "SELECT kernel.allow_vetted_write_op('kt.w_fence_add(bigint, text)', 'kt.grants_h0', 'insert')"),
    ("an unknown operation", "SELECT kernel.allow_vetted_write_op('kt.w_fence_add(bigint, text)', 'kt.fences', 'merge')"),
    ("a delete with no detail", "SELECT kernel.allow_vetted_write_op('kt.w_fence_remove(bigint)', 'kt.fences', 'delete')"),
    ("a delete by key and by transaction", "SELECT kernel.allow_vetted_write_op('kt.w_changes_ack()', 'kt.changes', 'delete', ARRAY['txid']::name[], NULL, 'txid')"),
    ("a delete by part of the key", "SELECT kernel.allow_vetted_write_op('kt.w_grants_delete(bigint[], bigint[], smallint[])', 'kt.grants', 'delete', ARRAY['node_sk']::name[])"),
    ("a delete by the key including org_id", "SELECT kernel.allow_vetted_write_op('kt.w_fence_remove(bigint)', 'kt.fences', 'delete', ARRAY['org_id', 'id']::name[])"),
    ("a transaction column that is not xid8", "SELECT kernel.allow_vetted_write_op('kt.w_changes_ack()', 'kt.changes', 'delete', NULL, NULL, 'node_id')"),
    ("an upsert that sets a key column", "SELECT kernel.allow_vetted_write_op('kt.w_grants_set(bigint[], bigint[], smallint[], smallint[])', 'kt.grants', 'upsert', NULL, ARRAY['node_sk']::name[])"),
    ("an upsert that sets a column the table lacks", "SELECT kernel.allow_vetted_write_op('kt.w_grants_set(bigint[], bigint[], smallint[], smallint[])', 'kt.grants', 'upsert', NULL, ARRAY['nope']::name[])"),
    ("an upsert with no column list", "SELECT kernel.allow_vetted_write_op('kt.w_grants_set(bigint[], bigint[], smallint[], smallint[])', 'kt.grants', 'upsert')"),
    ("an insert with a key", "SELECT kernel.allow_vetted_write_op('kt.w_fence_add(bigint, text)', 'kt.fences', 'insert', ARRAY['id']::name[])"),
    ("a cap above 5000", "SELECT kernel.allow_vetted_write_op('kt.w_fence_remove(bigint)', 'kt.fences', 'delete', ARRAY['id']::name[], NULL, NULL, 5001)"),
    ("a function owned by another schema's vetted role", "SELECT kernel.allow_vetted_write_op('kt.w_fence_add(bigint, text)', 'ku.items', 'insert')"),
]


@pytest.mark.parametrize("case", REFUSED, ids=[r[0] for r in REFUSED])
def test_allow_vetted_write_op_refuses(pdb, case):
    with conn(pdb) as c:
        c.execute("BEGIN")
        try:
            c.execute("SET LOCAL ROLE pipeshub_owner")
            with pytest.raises((pg.RaiseException, pg.CheckViolation)):
                c.execute(case[1])
        finally:
            c.execute("ROLLBACK")


def test_only_the_owner_can_pin(pdb):
    with conn(pdb) as c:
        for role in (*SERVICES, "pipeshub_jobs", "pipeshub_vetted_kt"):
            c.execute("BEGIN")
            try:
                c.execute(f"SET LOCAL ROLE {role}")
                with pytest.raises(pg.InsufficientPrivilege):
                    c.execute("SELECT kernel.allow_vetted_write_op('kt.w_fence_add(bigint, text)', 'kt.fences', 'insert')")
            finally:
                c.execute("ROLLBACK")
            c.execute("BEGIN")
            try:
                c.execute(f"SET LOCAL ROLE {role}")
                with pytest.raises(pg.InsufficientPrivilege):
                    c.execute("DELETE FROM kernel.vetted_write_pins")
            finally:
                c.execute("ROLLBACK")


# ---- the manifest ------------------------------------------------------------------------------------------------------------

def entry(**over) -> dict:
    base = {"name": "kt.w_x", "class": "write", "scope": "org", "writer": "pipeshub_connectors", "callers": ["python/app/x.py"], "returns": "int", "no_org": "0",
            "args": [{"name": "p_id", "type": "bigint"}], "body": "RETURN 0;", "pins": [{"op": "delete", "rel": "kt.fences", "key": ["id"]}]}
    base.update(over)
    return {k: v for k, v in base.items() if v is not None}


@pytest.mark.parametrize("over, why", [
    ({"class": "read", "writer": None}, "only an org-scoped write function carries pins"),
    ({"scope": "global"}, "only an org-scoped write function carries pins"),
    ({"pins": [{"op": "delete", "rel": "ku.items", "key": ["id"]}]}, "table of the function's schema"),
    ({"pins": [{"op": "delete", "rel": "kt.fences"}]}, "wrong detail"),
    ({"pins": [{"op": "delete", "rel": "kt.fences", "key": ["id"], "txn": "txid"}]}, "wrong detail"),
    ({"pins": [{"op": "upsert", "rel": "kt.fences", "key": ["id"]}]}, "wrong detail"),
    ({"pins": [{"op": "upsert", "rel": "kt.fences"}]}, "wrong detail"),
    ({"pins": [{"op": "insert", "rel": "kt.fences", "set": ["reason"]}]}, "wrong detail"),
    ({"pins": [{"op": "delete", "rel": "kt.fences", "key": ["org_id"]}]}, "not org_id"),
    ({"pins": [{"op": "drop", "rel": "kt.fences"}]}, "operation of"),
    ({"pins": [{"op": "delete", "rel": "kt.fences", "key": ["id"], "max_items": 5001}]}, "max_items"),
    ({"pins": [{"op": "delete", "rel": "kt.fences", "key": ["id"]}, {"op": "delete", "rel": "kt.fences", "key": ["id"]}]}, "pinned twice"),
    ({"pins": []}, "non-empty"),
    ({"callers": None}, "names its callers"),
    ({"body": "EXECUTE 'DELETE FROM kt.fences'; RETURN 0;"}, "cannot classify"),
    ({"body": "RAISE NOTICE 'x'; RETURN 0;"}, "RAISE is allowed only"),
])
def test_a_bad_pin_declaration_is_refused_by_the_manifest(over, why):
    with pytest.raises(vetted.ManifestError, match=why):
        vetted.parse(entry(**over), "m")


def test_the_generated_sql_pins_after_it_registers():
    sql = vetted.render(FNS[SIG[GD]])
    assert sql.index("kernel.register_function") < sql.index("kernel.allow_vetted_write_op")
    assert "ARRAY['principal_sk', 'node_sk', 'gtype']::name[], NULL, NULL, 5000" in sql
    assert "ARRAY[]::name[]" in vetted.render(FNS[SIG[KA]])               # DO NOTHING is an empty list, not NULL
    assert vetted.render(FNS[SIG["kt.notes_by_title"]]).count("allow_vetted_write_op") == 0


def test_array_items_are_validated_with_22023():
    sql = vetted.render(FNS[SIG[GS]])
    assert "EXISTS (SELECT 1 FROM unnest(p_principals) e WHERE e IS NULL)" in sql
    assert "e NOT BETWEEN 1 AND 4" in sql and "e NOT IN ('1', '2', '3')" in sql
    assert sql.count("USING ERRCODE = '22023'") >= 12
    for bad in ({"item_not_null": True}, {"item_length": [1, 2]}):
        with pytest.raises(vetted.ManifestError, match="array argument"):
            vetted.parse(entry(args=[{"name": "p_id", "type": "bigint", **bad}], pins=None), "m")


def test_a_pinned_function_that_is_not_in_the_manifest_or_differs_is_a_problem(pdb):
    with conn(pdb) as c:
        for sql, rule in [
            (f"DELETE FROM kernel.vetted_write_pins WHERE proc = '{SIG[FR]}'::regprocedure", "71 manifest and pin registry disagree"),
            (f"UPDATE kernel.vetted_write_pins SET max_items = 100 WHERE proc = '{SIG[GD]}'::regprocedure", "71 manifest and pin registry disagree"),
            (f"INSERT INTO kernel.vetted_write_pins (proc, rel, op) VALUES ('{SIG['kt.w_fence_add']}'::regprocedure, 'kt.keys', 'insert')", "71 manifest and pin registry disagree"),
        ]:
            c.execute("BEGIN")
            try:
                c.execute(sql)
                assert any(r == rule for r, _ in vetted.problems(c, FNS)), sql
            finally:
                c.execute("ROLLBACK")
        keep = dict(FNS)
        gone = {k: v for k, v in keep.items() if v.qualified != "kt.w_fence_add"}
        assert any(r == "71 vetted function is not in any manifest" for r, _ in vetted.problems(c, gone))


def test_only_the_named_callers_may_mention_a_pinned_function(tmp_path):
    (tmp_path / "python/app/services/graph_db/postgres").mkdir(parents=True)
    (tmp_path / "python/app/services/graph_db/postgres/kg_write.py").write_text("cur.execute('SELECT kt.w_grants_set(%s, %s, %s, %s)')")
    (tmp_path / "python/app/other.py").write_text("cur.execute('SELECT kt.w_grants_delete(%s, %s, %s)')")
    (tmp_path / "python/tests").mkdir()
    (tmp_path / "python/tests/test_x.py").write_text("w_grants_delete")
    got = vetted.callers_violations({k: v for k, v in FNS.items() if v.schema == "kt"}, tmp_path)
    assert [v.split(":")[0] for v in got] == ["python/app/other.py"], got


# ---- what the roles can do ----------------------------------------------------------------------------------------------------

@contextmanager
def unit(c: psycopg.Connection, role: str, org: str | None):
    """A transaction the way the runtime opens one: the service role and the org set transaction-locally; committed unless it failed."""
    c.execute("BEGIN")
    try:
        c.execute(f"SET LOCAL ROLE {role}")
        if org is not None:
            c.execute("SELECT set_config('app.org_id', %s, true)", (org,))
        yield c
    except BaseException:
        c.execute("ROLLBACK")
        raise
    c.execute("COMMIT")


ACCESS = {"kt.grants": "(org_id, principal_sk, node_sk, gtype, role) VALUES (%s, 1, 1, 1, 1)", "kt.fences": "(org_id, id, reason) VALUES (%s, 1, 'x')",
          "kt.keys": "(org_id, coll, id) VALUES (%s, 1, 'x')", "kt.prefs": "(org_id, k, v) VALUES (%s, 'k', 'v')"}


@pytest.mark.parametrize("role", (*SERVICES, "pipeshub_jobs"))
@pytest.mark.parametrize("table", ACCESS)
def test_no_service_role_writes_an_access_table_directly(pdb, role, table):
    with conn(pdb) as c:
        for sql in (f"INSERT INTO {table} {ACCESS[table]}", f"UPDATE {table} SET org_id = org_id", f"DELETE FROM {table}", f"TRUNCATE {table}",
                    f"INSERT INTO {table} {ACCESS[table]} ON CONFLICT DO NOTHING"):
            with unit(c, role, ORG_A):
                with pytest.raises(pg.InsufficientPrivilege):
                    c.execute(sql, (ORG_A,) if "%s" in sql else ())


def test_the_connectors_cannot_write_a_partition_directly_either(pdb):
    with conn(pdb) as c:
        for leaf in ("kt.grants_h0", "kt.grants_h1", "kt.grants_def"):
            with unit(c, WRITER, ORG_A), pytest.raises(pg.InsufficientPrivilege):
                c.execute(f"INSERT INTO {leaf} (org_id, principal_sk, node_sk, gtype, role) VALUES (%s, 1, 1, 1, 1)", (ORG_A,))


def test_the_change_log_is_inserted_by_the_writers_and_deleted_by_nobody(pdb):
    with conn(pdb) as c:
        for role in ("pipeshub_connectors", "pipeshub_query", "pipeshub_indexing"):
            with unit(c, role, ORG_A):
                c.execute("INSERT INTO kt.changes (org_id, coll, node_id) VALUES (%s, 1, 'n')", (ORG_A,))
            with unit(c, role, ORG_A), pytest.raises(pg.InsufficientPrivilege):
                c.execute("DELETE FROM kt.changes")
        with unit(c, "pipeshub_node", ORG_A), pytest.raises(pg.InsufficientPrivilege):
            c.execute("INSERT INTO kt.changes (org_id, coll, node_id) VALUES (%s, 1, 'n')", (ORG_A,))


CALLS = {GS: ([1], [2], [1], [1]), GD: ([1], [2], [1]), FR: (1,), "kt.w_fence_add": (77, "r"), "kt.w_fence_reason": (1, "r"), CA: (), KA: ([1], ["x"]), PP: ("k", "v")}


@pytest.mark.parametrize("name", CALLS)
@pytest.mark.parametrize("role", ("pipeshub_node", "pipeshub_indexing", "pipeshub_query", "pipeshub_jobs", "pipeshub_backup", "pipeshub_restore", "pipeshub_ro"))
def test_only_the_connectors_service_executes_a_pinned_function(pdb, name, role):
    with conn(pdb) as c, unit(c, role, ORG_A), pytest.raises(pg.InsufficientPrivilege):
        c.execute(f"SELECT {name}({', '.join(['%s'] * len(CALLS[name]))})", CALLS[name])


def test_no_pinned_function_takes_an_org_argument(pdb):
    with conn(pdb) as c:
        for name in CALLS:
            names = c.execute("SELECT proargnames FROM pg_proc WHERE oid = %s::regprocedure", (SIG[name],)).fetchone()[0] or []
            assert not [n for n in names if any(w in n for w in ("org", "workspace", "account"))], (name, names)


def call(c, name: str, org: str | None, args=None):
    a = CALLS[name] if args is None else args
    with unit(c, WRITER, org):
        return c.execute(f"SELECT {name}({', '.join(['%s'] * len(a))})", a).fetchone()[0]


def seed_two_orgs(c):
    c.execute("DELETE FROM kt.grants; DELETE FROM kt.fences; DELETE FROM kt.keys; DELETE FROM kt.prefs; DELETE FROM kt.changes")
    for org in (ORG_A, ORG_B):
        c.execute("INSERT INTO kt.grants (org_id, principal_sk, node_sk, gtype, role) VALUES (%s, 1, 2, 1, 1), (%s, 3, 4, 1, 1)", (org, org))
        c.execute("INSERT INTO kt.fences (org_id, id, reason) VALUES (%s, 1, 'f')", (org,))
        c.execute("INSERT INTO kt.keys (org_id, coll, id) VALUES (%s, 1, 'x')", (org,))
        c.execute("INSERT INTO kt.prefs (org_id, k, v) VALUES (%s, 'k', 'orig')", (org,))


def table_state(c) -> list:
    return [c.execute(f"SELECT * FROM {t} ORDER BY 1, 2, 3").fetchall() for t in ("kt.grants", "kt.fences", "kt.keys", "kt.prefs")]


def test_another_org_cannot_be_targeted(pdb):
    """Org A calls every writer with the keys of org B's rows: B's tables do not change; the rows A wrote are A's."""
    with conn(pdb) as c:
        seed_two_orgs(c)
        before_b = {t: c.execute(f"SELECT * FROM {t} WHERE org_id = %s ORDER BY 1, 2, 3", (ORG_B,)).fetchall() for t in ("kt.grants", "kt.fences", "kt.keys", "kt.prefs")}
        assert call(c, GD, ORG_A, ([1], [2], [1])) == 1 and call(c, GD, ORG_A, ([1], [2], [1])) == 0      # A's own row, once
        assert call(c, FR, ORG_A, (1,)) == 1
        assert call(c, "kt.w_fence_reason", ORG_A, (1, "A wrote this")) is False                        # A's fence is gone; B's id 1 is not reachable
        assert call(c, GS, ORG_A, ([3], [4], [1], [3])) == 1 and call(c, PP, ORG_A, ("k", "A")) is True and call(c, KA, ORG_A, ([1], ["x"])) == 0
        after_b = {t: c.execute(f"SELECT * FROM {t} WHERE org_id = %s ORDER BY 1, 2, 3", (ORG_B,)).fetchall() for t in before_b}
        assert after_b == before_b
        assert c.execute("SELECT role FROM kt.grants WHERE org_id = %s AND principal_sk = 3", (ORG_A,)).fetchone() == (3,)
        assert c.execute("SELECT count(*) FROM kt.grants WHERE role = 3 AND org_id <> %s", (ORG_A,)).fetchone() == (0,)
        seed_two_orgs(c)


def test_a_call_outside_an_org_context_refuses(pdb):
    """No org (unset, empty) answers like no rows and writes nothing; a malformed org is refused by the table's own CHECK."""
    with conn(pdb) as c:
        seed_two_orgs(c)
        before = table_state(c)
        for org in (None, ""):
            for name, args in CALLS.items():
                if name in ("kt.w_fence_add",):
                    assert call(c, name, org, args) is False
                else:
                    got = call(c, name, org, args)
                    assert got in (0, False), (name, org, got)
        assert table_state(c) == before
        for bad in ("x", "A" * 24, ORG_A + "0", "' OR true --"):
            with unit(c, WRITER, bad):
                with pytest.raises((pg.CheckViolation, pg.RaiseException)):
                    c.execute("SELECT kt.w_grants_set(%s, %s, %s, %s)", ([1], [2], [1], [1]))
            assert call(c, GD, bad, ([1], [2], [1])) == 0              # a delete matches nothing
            assert call(c, FR, bad, (1,)) == 0
        assert table_state(c) == before


INJECTION = ["'; DROP TABLE kt.grants; --", "x' OR '1'='1", "\\'); DELETE FROM kt.prefs; --", "$$ ; $$", "a\x01b", "é" * 20, "%s", "{}"]


@pytest.mark.parametrize("text", INJECTION)
def test_text_arguments_are_data(pdb, text):
    with conn(pdb) as c:
        seed_two_orgs(c)
        before_b = c.execute("SELECT * FROM kt.prefs WHERE org_id = %s", (ORG_B,)).fetchall()
        try:
            call(c, PP, ORG_A, (text[:60], text))
        except psycopg.Error as e:
            assert isinstance(e, (pg.InvalidParameterValue, pg.DataError, pg.CharacterNotInRepertoire)), e
        try:
            call(c, KA, ORG_A, ([1], [text]))
        except psycopg.Error as e:
            assert isinstance(e, (pg.InvalidParameterValue, pg.DataError, pg.CharacterNotInRepertoire)), e
        assert c.execute("SELECT to_regclass('kt.grants') IS NOT NULL").fetchone()[0]
        assert c.execute("SELECT count(*) FROM kt.prefs WHERE org_id = %s", (ORG_B,)).fetchone()[0] == len(before_b) == 1
        assert c.execute("SELECT count(*) FROM kt.grants").fetchone()[0] == 4
        seed_two_orgs(c)


@pytest.mark.parametrize("args, why", [
    (([1] * 5001, [2] * 5001, [1] * 5001, [1] * 5001), "more than 5,000 items"),
    (([1, 2], [2], [1], [1]), "arrays of different length"),
    (([None], [2], [1], [1]), "a NULL item"),
    (([1], [2], [9], [1]), "a gtype out of range"),
    (([1], [2], [1], [7]), "a role outside the list"),
    ((None, [2], [1], [1]), "a NULL array"),
])
def test_array_arguments_are_validated_before_anything_is_written(pdb, args, why):
    with conn(pdb) as c:
        seed_two_orgs(c)
        before = table_state(c)
        with unit(c, WRITER, ORG_A), pytest.raises(pg.InvalidParameterValue):
            c.execute("SELECT kt.w_grants_set(%s, %s, %s, %s)", args)
        assert table_state(c) == before, why
        seed_two_orgs(c)


def test_a_key_array_of_the_cap_deletes_exactly_those_rows(pdb):
    with conn(pdb) as c:
        seed_two_orgs(c)
        c.execute("INSERT INTO kt.grants (org_id, principal_sk, node_sk, gtype, role) SELECT %s, g, g, 2, 1 FROM generate_series(10, 5009) g", (ORG_A,))
        c.execute("INSERT INTO kt.grants (org_id, principal_sk, node_sk, gtype, role) SELECT %s, g, g, 2, 1 FROM generate_series(10, 5009) g", (ORG_B,))
        ks = list(range(10, 5010))
        assert call(c, GD, ORG_A, (ks, ks, [2] * 5000)) == 5000
        assert c.execute("SELECT count(*) FILTER (WHERE org_id = %s), count(*) FILTER (WHERE org_id = %s) FROM kt.grants WHERE gtype = 2", (ORG_A, ORG_B)).fetchone() == (0, 5000)
        with unit(c, WRITER, ORG_A), pytest.raises(pg.InvalidParameterValue):
            c.execute("SELECT kt.w_grants_delete(%s, %s, %s)", ([*ks, 9999], [*ks, 9999], [2] * 5001))
        seed_two_orgs(c)


def test_the_changes_of_this_transaction_are_acknowledged_and_no_others(pdb):
    with conn(pdb) as c:
        seed_two_orgs(c)
        c.execute("INSERT INTO kt.changes (org_id, coll, node_id) VALUES (%s, 1, 'earlier-a'), (%s, 1, 'earlier-b')", (ORG_A, ORG_B))      # another transaction's rows
        c.execute("BEGIN")
        try:
            c.execute("SET LOCAL ROLE pipeshub_connectors")
            c.execute("SELECT set_config('app.org_id', %s, true)", (ORG_A,))
            c.execute("INSERT INTO kt.changes (org_id, coll, node_id) VALUES (%s, 1, 'mine-1'), (%s, 2, 'mine-2')", (ORG_A, ORG_A))
            assert c.execute("SELECT kt.w_changes_ack()").fetchone()[0] == 2
            assert c.execute("SELECT node_id FROM kt.changes WHERE org_id = %s", (ORG_A,)).fetchall() == [("earlier-a",)]
            assert c.execute("SELECT kt.w_changes_ack()").fetchone()[0] == 0
        finally:
            c.execute("ROLLBACK")
        assert c.execute("SELECT count(*) FROM kt.changes").fetchone()[0] == 2
        c.execute("DELETE FROM kt.changes")


def test_a_pinned_function_cannot_be_redirected_by_the_callers_search_path_or_temp_tables(pdb):
    with conn(pdb) as c:
        seed_two_orgs(c)
        with unit(c, WRITER, ORG_A):
            c.execute("SET LOCAL search_path = pg_temp, public")
            c.execute("CREATE TEMP TABLE grants (org_id text, principal_sk bigint, node_sk bigint, gtype smallint, role smallint)")
            assert c.execute("SELECT kt.w_grants_delete(%s, %s, %s)", ([1], [2], [1])).fetchone()[0] == 1
            assert c.execute("SELECT count(*) FROM pg_temp.grants").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM kt.grants WHERE org_id = %s", (ORG_A,)).fetchone()[0] == 1
        seed_two_orgs(c)


def test_a_pinned_function_does_not_widen_the_vetted_roles_reach(pdb):
    """The vetted owner has DELETE on three tables of its schema; the rest of its schema still has none, and it has nothing outside kt."""
    with conn(pdb) as c:
        outside = c.execute("SELECT c.oid::regclass::text FROM pg_class c, aclexplode(c.relacl) a WHERE a.grantee = 'pipeshub_vetted_kt'::regrole "
                            "AND c.relnamespace <> 'kt'::regnamespace").fetchall()
        assert outside == []


def test_the_replacement_of_a_pinned_function_keeps_its_pins(pdb):
    """Re-running the generated file (CREATE OR REPLACE) is how a body changes; the pins stay and the catalog is still clean."""
    with conn(pdb) as c:
        before = c.execute("SELECT count(*) FROM kernel.vetted_write_pins").fetchone()[0]
        c.execute("BEGIN")
        try:
            install(c, vetted.render(FNS[SIG[GD]], replace=True))
            assert c.execute("SELECT count(*) FROM kernel.vetted_write_pins").fetchone()[0] == before
            assert violations(c) == []
        finally:
            c.execute("ROLLBACK")


def test_a_dropped_and_recreated_function_needs_its_pins_again(pdb):
    with conn(pdb) as c:
        c.execute("BEGIN")
        try:
            c.execute("DROP FUNCTION kt.w_fence_remove(bigint)")
            assert any(r.startswith("100") for r, _ in violations(c))
        finally:
            c.execute("ROLLBACK")


def test_the_plan_of_the_change_log_delete_is_an_index_scan_on_a_big_table(pdb):
    """The generated suite gates plans through ``reads``; the delete of the current transaction has none (its xid8 column cannot be forged)."""
    with conn(pdb) as c:
        c.execute("DELETE FROM kt.changes")
        for org in (ORG_A, ORG_B):
            c.execute("INSERT INTO kt.changes (org_id, coll, node_id) SELECT %s, 1, 'n' || g FROM generate_series(1, 20000) g", (org,))
        c.execute("ANALYZE kt.changes")
        try:
            fn = FNS[SIG[CA]]
            assert vetted_harness.check_plans(c, fn, ORG_A, "SELECT kt.w_changes_ack()", [], WRITER, "changes_pkey", "auto_explain") == []
        finally:
            c.execute("DELETE FROM kt.changes")
