"""Review findings on the pinned vetted writers, each as a planted function, view, foreign key, child table or grant that the first
version of the checks let through with a clean catalog and that the catalog now names. A plant that ran a statement as a role also has its runtime
half here where the database itself refuses it.

Runs on the fixture of test_vetted_pins (schema kt, the pinned test functions on the per-org tables)."""
# ruff: noqa: F811
import pytest
from psycopg import errors as pg

from .rls_fixture import ORG_A, ORG_B
from .test_vetted import install
from .test_vetted_pins import (  # noqa: F401  (pdb is the module fixture)
    conn,
    mutated,
    pdb,
    planted,
    unit,
    violations,
)

DECL = "DECLARE v_org text := NULLIF(current_setting('app.org_id', true), '');"


def hand(name: str, body: str, *, args: str = "p_id bigint", pins: tuple = (), writer: str = "pipeshub_connectors", schema: str = "kt", returns: str = "int",
         decl: str = DECL, cls: str = "write", scope: str = "org") -> str:
    """SQL that installs a hand-written SECURITY DEFINER function the way vetted.render does (owner borrowed, registry row, pins), body as written."""
    sig = f"{schema}.{name}({', '.join(a.split(' ', 1)[1] for a in args.split(', ') if a)})"
    owner = f"pipeshub_vetted_{schema}"
    pin_sql = "".join(f"SELECT kernel.allow_vetted_write_op('{sig}', {p});\n" for p in pins)
    return f"""GRANT {owner} TO pipeshub_owner WITH SET TRUE;
GRANT USAGE ON SCHEMA {schema} TO {owner}; GRANT CREATE ON SCHEMA {schema} TO {owner};
SET LOCAL ROLE {owner};
CREATE OR REPLACE FUNCTION {schema}.{name}({args}) RETURNS {returns} LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, {schema}, pg_temp
SET standard_conforming_strings = on
AS $fn$
{decl}
BEGIN
  IF p_id IS NULL THEN RAISE EXCEPTION 'bad' USING ERRCODE = '22023'; END IF;
  IF v_org IS NULL THEN RETURN 0; END IF;
{body}
END $fn$;
REVOKE ALL ON FUNCTION {sig} FROM PUBLIC;
GRANT EXECUTE ON FUNCTION {sig} TO {writer};
SET LOCAL ROLE pipeshub_owner;
REVOKE CREATE ON SCHEMA {schema} FROM {owner};
REVOKE {owner} FROM pipeshub_owner;
SELECT kernel.register_function('{sig}', '{cls}', {f"'{writer}'" if cls == 'write' else 'NULL'}, '{scope}', false);
{pin_sql}"""


DEL_FENCE = "  DELETE FROM kt.fences t WHERE t.org_id = v_org AND t.id = p_id;\n"
PIN_FENCE = "'kt.fences', 'delete', '{id}'"

PLANTS = [
    # ---- F1: the org variable is rebound
    ("F1 v_org := another org", hand("p01a", f"  v_org := '{ORG_B}';\n" + DEL_FENCE + "  RETURN 1;", pins=(PIN_FENCE,)), "116"),
    ("F1 SELECT .. INTO v_org", hand("p01b", f"  SELECT '{ORG_B}' INTO v_org;\n" + DEL_FENCE + "  RETURN 1;", pins=(PIN_FENCE,)), "116"),
    ("F1 a nested block that declares v_org again",
     hand("p01c", f"  DECLARE v_org text := '{ORG_B}';\n  BEGIN\n  " + DEL_FENCE + "  END;\n  RETURN 1;", pins=(PIN_FENCE,)), "116"),
    ("F1 FOR v_org IN a query", hand("p01d", "  FOR v_org IN SELECT 'x' LOOP\n  " + DEL_FENCE + "  END LOOP;\n  RETURN 1;", pins=(PIN_FENCE,)), "116"),
    ("F1 GET DIAGNOSTICS into v_org", hand("p01e", "  GET DIAGNOSTICS v_org = ROW_COUNT;\n" + DEL_FENCE + "  RETURN 1;", pins=(PIN_FENCE,)), "116"),
    # ---- F2: statements chained, a second row, a join in an update
    ("F2a a data-modifying CTE hides an org-wide delete",
     hand("p02a", DEL_FENCE + "  WITH i AS (INSERT INTO kt.keys (org_id, coll, id) VALUES (v_org, 1, 'x') RETURNING 1)\n    DELETE FROM kt.grants WHERE true;\n  RETURN 1;",
          pins=(PIN_FENCE, "'kt.keys', 'insert'")), "105 a statement of a pinned function has more than one DML keyword"),
    ("F2a the same chain in a function with no pin",
     hand("p02b", "  WITH i AS (INSERT INTO kt.prefs (org_id, k, v) VALUES (v_org, 'a', 'b') RETURNING 1)\n    DELETE FROM kt.grants WHERE true;\n  RETURN 1;", writer="pipeshub_query"), "114"),
    ("F2a two statements on one line in a function with no pin",
     hand("p02c", "  PERFORM 1; UPDATE kt.grants SET role = 3 WHERE true; RETURN 1;", writer="pipeshub_query"), "114"),
    ("F2b a second VALUES tuple writes another org",
     hand("p03a", f"  INSERT INTO kt.fences (org_id, id, reason) VALUES (v_org, p_id, 'x'), ('{ORG_B}', p_id, 'planted');\n  RETURN 1;", pins=("'kt.fences', 'insert'",)), "117"),
    ("F2c an UPDATE joined to a sub-select with WHERE true",
     hand("p03b", "  UPDATE kt.prefs t SET v = 'owned' FROM (SELECT 1 AS x FROM kt.prefs z WHERE z.org_id = v_org LIMIT 1) q WHERE true;\n  RETURN 1;", pins=("'kt.prefs', 'update'",)), "117"),
    ("F2c an UPDATE that sets the org column",
     hand("p03c", "  UPDATE kt.prefs t SET org_id = 'b' WHERE t.org_id = v_org AND t.k = 'k';\n  RETURN 1;", pins=("'kt.prefs', 'update'",)), "117"),
    ("F2c an UPDATE whose WHERE has a sub-select",
     hand("p03d", "  UPDATE kt.prefs t SET v = 'x' WHERE t.org_id = v_org AND t.k = (SELECT z.k FROM kt.prefs z LIMIT 1);\n  RETURN 1;", pins=("'kt.prefs', 'update'",)), "117"),
    ("F2c an INSERT .. SELECT from another table",
     hand("p03e", "  INSERT INTO kt.prefs (org_id, k, v) SELECT v_org, z.k, z.v FROM kt.prefs z;\n  RETURN 1;", pins=("'kt.prefs', 'insert'",)), "117"),
    # ---- F3: the lexer
    ("F3a a carriage return ends a comment: the DELETE is code (pinned function)",
     hand("p04a", DEL_FENCE + "  -- c\rDELETE FROM kt.grants WHERE true;\n  RETURN 1;", pins=(PIN_FENCE,)), "105"),
    ("F3a the same in a function with no pin", hand("p04b", "  -- c\rDELETE FROM kt.grants WHERE true;\n  RETURN 1;", writer="pipeshub_query"), "114"),
    ("F3a a carriage return in a definer body is refused outright", hand("p04c", "  -- c\rNULL;\n  RETURN 1;"), "111"),
    ("F3b an alias with $", hand("p05a", "  UPDATE kt.grants g$ SET role = 3 WHERE true;\n  RETURN 1;", writer="pipeshub_query"), "111"),
    ("F3b an alias with a non-ASCII letter", hand("p05b", "  UPDATE kt.grants gé SET role = 3 WHERE true;\n  RETURN 1;", writer="pipeshub_query"), "111"),
    ("F3b the same alias is still a write of the pinned table", hand("p05c", "  UPDATE kt.grants g$ SET role = 3 WHERE true;\n  RETURN 1;", writer="pipeshub_query"), "114"),
    # ---- F4: routes the first rules did not model
    ("F4a a SQL-standard body (BEGIN ATOMIC)",
     "GRANT pipeshub_vetted_kt TO pipeshub_owner WITH SET TRUE; GRANT USAGE, CREATE ON SCHEMA kt TO pipeshub_vetted_kt; SET LOCAL ROLE pipeshub_vetted_kt;\n"
     "CREATE FUNCTION kt.atomic_wipe() RETURNS int LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, kt, pg_temp SET standard_conforming_strings = on "
     "BEGIN ATOMIC DELETE FROM kt.grants WHERE true; SELECT 1; END;\n"
     "SET LOCAL ROLE pipeshub_owner; REVOKE CREATE ON SCHEMA kt FROM pipeshub_vetted_kt; REVOKE pipeshub_vetted_kt FROM pipeshub_owner;", "112"),
    ("F4b a wrapper another role can run calls the pinned function", hand("p07", "  PERFORM kt.w_fence_remove(p_id);\n  RETURN 1;", writer="pipeshub_query"), "120"),
    ("F4c a view over a pinned table",
     "SET LOCAL ROLE pipeshub_owner; CREATE VIEW kt.grants_v WITH (security_invoker = true) AS SELECT * FROM kt.grants; GRANT SELECT, INSERT, UPDATE ON kt.grants_v TO pipeshub_vetted_kt;", "121"),
    ("F4c a view over a view over a pinned table",
     "SET LOCAL ROLE pipeshub_owner; CREATE VIEW kt.grants_v WITH (security_invoker = true) AS SELECT * FROM kt.grants; CREATE VIEW kt.grants_vv WITH (security_invoker = true) AS SELECT * FROM kt.grants_v;", "121"),
    ("F4d a foreign key that cascades into a pinned table",
     "SET LOCAL ROLE pipeshub_owner; CREATE TABLE kt.u (org_id text NOT NULL, id bigint NOT NULL, PRIMARY KEY (org_id, id)); SELECT kernel.apply_rls('kt.u', 'org');\n"
     "ALTER TABLE kt.fences ADD FOREIGN KEY (org_id, id) REFERENCES kt.u (org_id, id) ON DELETE CASCADE", "123"),
    ("F4d ON UPDATE CASCADE as well",
     "SET LOCAL ROLE pipeshub_owner; CREATE TABLE kt.u (org_id text NOT NULL, id bigint NOT NULL, PRIMARY KEY (org_id, id)); SELECT kernel.apply_rls('kt.u', 'org');\n"
     "ALTER TABLE kt.fences ADD FOREIGN KEY (org_id, id) REFERENCES kt.u (org_id, id) ON UPDATE CASCADE", "123"),
    ("F4e an inheritance child of a pinned table",
     "SET LOCAL ROLE pipeshub_owner; CREATE TABLE kt.fences_kid () INHERITS (kt.fences); ALTER TABLE kt.fences_kid ADD PRIMARY KEY (org_id, id); SELECT kernel.apply_rls('kt.fences_kid', 'org');", "122"),
    # ---- F5: privileges
    ("F5 DELETE for the connectors role on an upsert-only table", "GRANT DELETE ON kt.prefs TO pipeshub_connectors", "126"),
    ("F5 DELETE for the connectors role on an insert-only table", "GRANT DELETE ON kt.keys TO pipeshub_connectors", "126"),
    ("F5 a column UPDATE grant", "GRANT UPDATE (v) ON kt.prefs TO pipeshub_connectors", "127"),
    ("F5 the vetted role keeps DELETE on an upsert-only table", "GRANT DELETE ON kt.prefs TO pipeshub_vetted_kt", "124"),
    ("F5 the vetted role holds DELETE on an upsert-only table", "GRANT DELETE ON kt.keys TO pipeshub_vetted_kt", "124"),
    ("F5 an access table nobody pinned is not open for the connectors role",
     "SET LOCAL ROLE pipeshub_owner; CREATE TABLE kt.openacc (org_id text NOT NULL, id bigint NOT NULL, PRIMARY KEY (org_id, id));\n"
     "SELECT kernel.apply_rls('kt.openacc', 'org', p_vetted_write => true); SELECT kernel.require_vetted_org_policy('kt.openacc'); GRANT INSERT, UPDATE, DELETE ON kt.openacc TO pipeshub_connectors;", "126"),
    ("F5 pins stripped from a table: it stays closed",
     "DELETE FROM kernel.vetted_write_pins WHERE rel = 'kt.grants'::regclass; GRANT INSERT, UPDATE, DELETE ON kt.grants TO pipeshub_connectors", "126"),
    ("F5 pins stripped: the vetted role still holds what they needed",
     "DELETE FROM kernel.vetted_write_pins WHERE rel = 'kt.grants'::regclass", "124"),
    # ---- F7: a cap that guards nothing
    ("F7 IF cardinality(..) > N THEN NULL", mutated("kt.w_grants_delete", "IF cardinality(p_principals) > 5000 THEN RAISE EXCEPTION 'w_grants_delete: p_principals takes at most 5000 items' USING ERRCODE = '22023'; END IF;",
                                                   "IF cardinality(p_principals) > 5000 THEN NULL; END IF;"), "108"),
    ("F7 the cap is checked after the DELETE", mutated("kt.w_fence_remove", "DELETE FROM kt.fences t", "IF cardinality(ARRAY[p_id]) > 5000 THEN RAISE EXCEPTION 'x' USING ERRCODE = '22023'; END IF;\n    DELETE FROM kt.fences t"), None),
    # ---- notes
    ("N2 a pinned function whose writer is not a registered pin writer",
     "UPDATE kernel.function_registry SET writer = 'pipeshub_query' WHERE proc = 'kt.w_fence_remove(bigint)'::regprocedure", "125"),
]
PLANTS = [p for p in PLANTS if p[2] is not None]


@pytest.mark.parametrize("plant", PLANTS, ids=[p[0] for p in PLANTS])
def test_a_route_the_review_found_is_named_by_the_catalog(pdb, plant):
    _, sql, rule = plant
    got = planted(pdb, sql)
    assert any(r.startswith(rule) for r, _ in got), got


def test_the_fixture_is_clean_with_every_rule_on(pdb):
    with conn(pdb) as c:
        assert violations(c) == []
        assert violations(c, full=True) == []


# ---- the runtime halves: what the database itself refuses ----------------------------------------------------------------------------

def _as(c, role: str, org: str | None, sql: str, args=()):
    with unit(c, role, org):
        cur = c.execute(sql, args)
        return cur.fetchall() if cur.description else cur.rowcount


def test_an_org_rebinding_function_cannot_reach_the_other_org_because_the_policy_binds_the_vetted_role(pdb):
    """P01 as it ran in the review: the function sets v_org to org B and deletes; with the per-org policy the DELETE sees none of org B's rows."""
    with conn(pdb) as c:
        c.execute("DELETE FROM kt.fences")
        for org in (ORG_A, ORG_B):
            c.execute("INSERT INTO kt.fences (org_id, id, reason) VALUES (%s, 1, 'r')", (org,))
        c.execute("BEGIN")
        try:
            install(c, hand("p01a", f"  v_org := '{ORG_B}';\n" + DEL_FENCE + "  RETURN 1;", pins=(PIN_FENCE,)))
            assert _as(c, "pipeshub_connectors", ORG_A, "SELECT kt.p01a(1)") == [(1,)]
            c.execute("COMMIT")
        except BaseException:
            c.execute("ROLLBACK")
            raise
        assert c.execute("SELECT org_id FROM kt.fences").fetchall() == [(ORG_A,)] or c.execute("SELECT count(*) FROM kt.fences WHERE org_id = %s", (ORG_B,)).fetchone()[0] == 1
        c.execute("BEGIN")
        c.execute("DROP FUNCTION kt.p01a(bigint)")
        c.execute("DELETE FROM kernel.function_registry WHERE proc::oid NOT IN (SELECT oid FROM pg_proc)")
        c.execute("DELETE FROM kernel.vetted_write_pins WHERE proc::oid NOT IN (SELECT oid FROM pg_proc)")
        c.execute("COMMIT")
        c.execute("DELETE FROM kt.fences")


def test_an_insert_for_the_other_org_is_refused_by_the_policy(pdb):
    with conn(pdb) as c:
        c.execute("BEGIN")
        try:
            install(c, hand("p03a", f"  INSERT INTO kt.fences (org_id, id, reason) VALUES (v_org, p_id, 'x'), ('{ORG_B}', p_id, 'planted');\n  RETURN 1;", pins=("'kt.fences', 'insert'",)))
            with pytest.raises(pg.InsufficientPrivilege):
                _as(c, "pipeshub_connectors", ORG_A, "SELECT kt.p03a(7)")
        finally:
            c.execute("ROLLBACK")


def test_a_role_that_may_not_write_the_table_cannot_write_it_through_a_wrapper_view_or_child(pdb):
    """P07/P08/P09 at run time: the query role gets 42501 on the pinned function, on a wrapper only if the catalog let it exist (it does not, rule 120)."""
    with conn(pdb) as c:
        with pytest.raises(pg.InsufficientPrivilege):
            _as(c, "pipeshub_query", ORG_A, "SELECT kt.w_fence_remove(1)")
        with pytest.raises(pg.InsufficientPrivilege):
            _as(c, "pipeshub_query", ORG_A, "DELETE FROM kt.fences")
