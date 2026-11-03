"""Further checks of the pinned vetted writers: statements the text checks never read (DO, SET and the rest), SQL-standard bodies in invoker routines, and the
rule text of a quoted target. Each plant is a function the first two versions of the checks let through with a clean catalog; the catalog now names it.

Runs on the fixture of test_vetted_pins (schema kt, the pinned test functions, every module of a migrated database)."""
import pytest

from .rls_fixture import ORG_A, ORG_B
from .test_vetted_pins import conn, planted, violations
from .test_vetted_pins import pdb as pdb  # the module fixture
from .test_vetted_pins_k2 import DEL_FENCE, PIN_FENCE, hand

OWNER = "SET LOCAL ROLE pipeshub_owner; "
PIN_GRANTS = "'kt.grants', 'delete', '{principal_sk,node_sk,gtype}'"
INV_ATOMIC = OWNER + "CREATE FUNCTION kt.inv_wipe() RETURNS void LANGUAGE sql BEGIN ATOMIC DELETE FROM kt.grants; END; "
CALL_INV = hand("x_call_inv", "  PERFORM kt.inv_wipe();\n  RETURN 1;", writer="pipeshub_query")

PLANTS = [
    # ---- DO: the code is a string literal
    ("DO in a pinned function", hand("d01", DEL_FENCE + "  DO 'BEGIN DELETE FROM kt.grants WHERE true; END';\n  RETURN 1;", pins=(PIN_FENCE,)), "130"),
    ("DO in an unpinned write function", hand("d02", "  DO 'BEGIN DELETE FROM kt.grants; DELETE FROM kt.fences; END';\n  RETURN 1;", writer="pipeshub_query"), "130"),
    ("DO in a read-class function", hand("d03", "  DO 'BEGIN INSERT INTO kt.fences VALUES (current_setting(''app.org_id''), 99, ''x''); END';\n  RETURN 1;",
                                          cls="read", writer="pipeshub_query"), "130"),
    ("DO LANGUAGE plpgsql with a body that sets the org", hand("d04", f"  DO LANGUAGE plpgsql 'BEGIN PERFORM set_config(''app.org_id'', ''{ORG_B}'', true); DELETE FROM kt.grants; END';\n  RETURN 1;",
                                                              writer="pipeshub_query"), "130"),
    ("DO that changes the org and deletes, in a pinned function", hand("d07", f"  DO 'BEGIN PERFORM set_config(''app.org_id'', ''{ORG_B}'', true); DELETE FROM kt.fences; END';\n  RETURN 1;",
                                                                      pins=(PIN_FENCE,)), "130"),
    ("DO behind a label", hand("d10", "  <<l>>\n  BEGIN DO 'BEGIN NULL; END'; END;\n  RETURN 1;", writer="pipeshub_query"), "130"),
    ("DO in an invoker routine called by a writer",
     OWNER + "CREATE FUNCTION kt.inv_do() RETURNS void LANGUAGE plpgsql AS 'BEGIN DO ''BEGIN DELETE FROM kt.grants; END''; END'; " + hand("x_call_do", "  PERFORM kt.inv_do();\n  RETURN 1;", writer="pipeshub_query"), "130"),
    # ---- SET and RESET: they change app.org_id, the search path, the role
    ("plain SET app.org_id then a DO", hand("d08", f"  SET app.org_id = '{ORG_B}';\n  DO 'BEGIN DELETE FROM kt.grants; END';\n  RETURN 1;", writer="pipeshub_query"), "130"),
    ("plain SET search_path first", hand("f01", "  SET search_path = kt, pg_catalog;\n" + DEL_FENCE + "  RETURN 1;", pins=(PIN_FENCE,)), "130"),
    ("plain SET app.org_id in a pinned function", hand("f02", f"  SET app.org_id = '{ORG_B}';\n" + DEL_FENCE + "  RETURN 1;", pins=(PIN_FENCE,)), "130"),
    ("plain SET app.org_id then a read", hand("f03", f"  SET app.org_id = '{ORG_B}';\n  RETURN (SELECT count(*) FROM kt.grants);", pins=(PIN_FENCE,)), "130"),
    ("SET ROLE", hand("f04", "  SET ROLE pipeshub_owner;\n" + DEL_FENCE + "  RETURN 1;", pins=(PIN_FENCE,)), "130"),
    ("SET LOCAL after THEN", hand("f05", "  IF p_id > 0 THEN SET LOCAL app.org_id = 'x'; END IF;\n  RETURN 1;", writer="pipeshub_query"), "130"),
    ("RESET", hand("f06", "  RESET app.org_id;\n  RETURN 1;", writer="pipeshub_query"), "130"),
    ("PREPARE", hand("f07", "  PREPARE q AS DELETE FROM kt.grants;\n  RETURN 1;", writer="pipeshub_query"), "130"),
    ("DDL (ALTER) at a statement start", hand("f08", "  ALTER TABLE kt.grants NO FORCE ROW LEVEL SECURITY;\n  RETURN 1;", writer="pipeshub_query"), "130"),
    ("GRANT at a statement start", hand("f09", "  GRANT DELETE ON kt.grants TO pipeshub_query;\n  RETURN 1;", writer="pipeshub_query"), "130"),
    ("SET after an EXCEPTION handler", hand("f10", "  BEGIN\n    NULL;\n  EXCEPTION WHEN OTHERS THEN SET app.org_id = 'x';\n  END;\n  RETURN 1;", writer="pipeshub_query"), "130"),
    # ---- set_config of anything but lock_timeout, wherever it is
    ("set_config of the org in a function", hand("g01", f"  PERFORM set_config('app.org_id', '{ORG_B}', true);\n  RETURN 1;", writer="pipeshub_query"), "131"),
    ("set_config with a computed name", hand("g02", "  PERFORM set_config('app.' || 'org_id', 'x', true);\n  RETURN 1;", writer="pipeshub_query"), "131"),
    ("set_config of the org in an invoker routine",
     OWNER + f"CREATE FUNCTION kt.inv_cfg() RETURNS void LANGUAGE plpgsql AS 'BEGIN PERFORM set_config(''app.org_id'', ''{ORG_B}'', true); END'; "
     + hand("x_call_cfg", "  PERFORM kt.inv_cfg();\n  RETURN 1;", writer="pipeshub_query"), "131"),
    # ---- SQL-standard bodies of invoker routines
    ("invoker BEGIN ATOMIC that deletes the pinned table, called by a writer", INV_ATOMIC + CALL_INV, "112"),
    ("the same with EXECUTE revoked from PUBLIC and granted to the vetted role",
     INV_ATOMIC + "REVOKE ALL ON FUNCTION kt.inv_wipe() FROM PUBLIC; GRANT EXECUTE ON FUNCTION kt.inv_wipe() TO pipeshub_vetted_kt; " + CALL_INV, "112"),
    ("invoker RETURN <expression> (a parse tree in prosqlbody)",
     OWNER + "CREATE FUNCTION kt.inv_ret() RETURNS bigint LANGUAGE sql RETURN (SELECT count(*) FROM kt.grants); "
     + hand("x_call_ret", "  PERFORM kt.inv_ret();\n  RETURN 1;", writer="pipeshub_query"), "112"),
    # ---- MERGE and ONLY: statements the shapes do not read (a pinned function, and a function with no pin)
    ("MERGE ... WHEN MATCHED THEN DELETE in a pinned function",
     hand("n01", "  MERGE INTO kt.fences t USING (SELECT p_id AS id) k ON t.org_id = v_org AND t.id = k.id WHEN MATCHED THEN DELETE;\n  RETURN 1;", pins=(PIN_FENCE,)), "105"),
    ("MERGE ... UPDATE of a table the function does not pin",
     hand("n02", DEL_FENCE + "  MERGE INTO kt.grants t USING (SELECT p_id AS id) k ON t.org_id = v_org WHEN MATCHED THEN UPDATE SET role = 9;\n  RETURN 1;", pins=(PIN_FENCE,)), "105"),
    ("MERGE ... INSERT in a pinned function",
     hand("n03", DEL_FENCE + "  MERGE INTO kt.fences t USING (SELECT p_id AS id) k ON t.org_id = v_org AND t.id = k.id WHEN NOT MATCHED THEN INSERT (org_id, id, reason) VALUES (v_org, k.id, 'x');\n  RETURN 1;",
          pins=(PIN_FENCE,)), "105"),
    ("UPDATE ONLY in a pinned function", hand("n04", DEL_FENCE + "  UPDATE ONLY kt.grants SET role = 9 WHERE org_id = v_org;\n  RETURN 1;", pins=(PIN_FENCE,)), "105"),
    ("DELETE FROM ONLY in a pinned function", hand("n05", "  DELETE FROM ONLY kt.fences t WHERE t.org_id = v_org AND t.id = p_id;\n  RETURN 1;", pins=(PIN_FENCE,)), "105"),
    ("UPDATE ONLY of a pinned table in a function with no pin", hand("n06", "  UPDATE ONLY kt.grants SET role = 9;\n  RETURN 1;", writer="pipeshub_query"), "114"),
    ("DELETE FROM ONLY of a pinned table in a function with no pin", hand("n07", "  DELETE FROM ONLY kt.fences;\n  RETURN 1;", writer="pipeshub_query"), "114"),
    ("MERGE ... DELETE of a pinned table in a function with no pin",
     hand("n08", "  MERGE INTO kt.grants t USING (SELECT 1 AS x) k ON true WHEN MATCHED THEN DELETE;\n  RETURN 1;", writer="pipeshub_query"), "114"),
    ("MERGE ... UPDATE of a pinned table in a function with no pin",
     hand("n09", "  MERGE INTO kt.grants t USING (SELECT 1 AS x) k ON true WHEN MATCHED THEN UPDATE SET role = 9;\n  RETURN 1;", writer="pipeshub_query"), "114"),
    ("MERGE ... INSERT into a pinned table in a function with no pin",
     hand("n10", "  MERGE INTO kt.fences t USING (SELECT 1 AS x) k ON false WHEN NOT MATCHED THEN INSERT (org_id, id, reason) VALUES ('x', 1, 'y');\n  RETURN 1;", writer="pipeshub_query"), "114"),
    # ---- a pinned function calls pinned functions and vetted kernel routines only
    ("a pinned function calls an invoker routine that deletes",
     "CREATE FUNCTION public.evil_del() RETURNS void LANGUAGE sql AS 'DELETE FROM kt.fences'; " + hand("c01", DEL_FENCE + "  PERFORM public.evil_del();\n  RETURN 1;", pins=(PIN_FENCE,)), "132"),
    ("a pinned function calls an invoker routine of its own schema",
     OWNER + "CREATE FUNCTION kt.helper() RETURNS int LANGUAGE sql AS 'SELECT 1'; " + hand("c02", DEL_FENCE + "  PERFORM kt.helper();\n  RETURN 1;", pins=(PIN_FENCE,)), "132"),
    # ---- no routine carries an app.* setting
    ("an invoker routine that sets app.org_id for its own run, called by a pinned function",
     f"CREATE FUNCTION public.evil_org() RETURNS void LANGUAGE sql SET app.org_id = '{ORG_B}' AS 'DELETE FROM kt.fences'; "
     + hand("c03", DEL_FENCE + "  PERFORM public.evil_org();\n  RETURN 1;", pins=(PIN_FENCE,)), "133"),
    ("a routine with an app.* setting that nothing calls", "CREATE FUNCTION public.lonely() RETURNS int LANGUAGE sql SET app.org_id = 'x' AS 'SELECT 1'; ", "133"),
]


NEW_TABLE = OWNER + "CREATE TABLE kt.newacc (org_id text NOT NULL, id bigint NOT NULL, PRIMARY KEY (org_id, id)); SELECT kernel.apply_rls('kt.newacc', 'org', p_vetted_write => true); "
PLANTS.append(("a new table of a schema that owns pins forgets require_vetted_org_policy", NEW_TABLE, "128"))


@pytest.mark.parametrize("plant", PLANTS, ids=[p[0] for p in PLANTS])
def test_a_route_the_second_review_found_is_named_by_the_catalog(pdb, plant):
    _, sql, rule = plant
    got = planted(pdb, sql)
    assert any(r.startswith(rule) for r, _ in got), got


def test_a_quoted_target_leaves_a_rule_text_and_is_still_a_violation(pdb):
    got = planted(pdb, hand("q01", '  DELETE FROM "kt"."grants" WHERE true;\n  RETURN 1;', pins=(PIN_GRANTS,)))
    assert got and all(r is not None for r, _ in got), got
    assert any(r.startswith(("105", "98")) for r, _ in got), got


def test_what_the_new_rules_forbid_is_in_none_of_the_routines_the_modules_install(pdb):
    """Zero hits on every SQL and PL/pgSQL routine of a migrated database (all modules plus the fixture)."""
    with conn(pdb) as c:
        assert [v for v in violations(c, full=True) if v[0][:3] in ("112", "130", "131")] == []
        n = c.execute("SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace JOIN pg_language l ON l.oid = p.prolang "
                      "WHERE l.lanname IN ('sql', 'plpgsql') AND n.nspname NOT IN ('pg_catalog', 'information_schema')").fetchone()[0]
        assert n > 50, n        # the scan really saw the routines of every module (a sanity bound, not a count)


def test_the_manifest_refuses_a_do_a_set_and_a_set_config():
    from db.lib import vetted
    for body, word in (("BEGIN DO 'x'; RETURN 1; END;", "DO"), ("BEGIN SET app.org_id = 'x'; RETURN 1; END;", "SET"), ("BEGIN RESET ALL; RETURN 1; END;", "RESET"),
                       ("BEGIN IF true THEN SET LOCAL x.y = 1; END IF; RETURN 1; END;", "SET"), ("BEGIN PREPARE q AS SELECT 1; RETURN 1; END;", "PREPARE")):
        with pytest.raises(vetted.ManifestError, match=word):
            vetted.deny_unclassifiable(body, "t")
    with pytest.raises(vetted.ManifestError, match="set_config"):
        vetted.deny_unclassifiable("BEGIN PERFORM set_config('x.y', 'z', true); RETURN 1; END;", "t")
    vetted.deny_unclassifiable("BEGIN INSERT INTO t (a) VALUES (1) ON CONFLICT (a) DO NOTHING; UPDATE t SET a = 2 WHERE a = 1; RETURN 1; END;", "t")


def test_the_boundary_is_the_text_checks_not_the_policy_a_routine_that_sets_the_org_crosses_it_when_the_catalog_is_bypassed(pdb):
    """What the per-org policy can and cannot do (README, "Pinned writes"). The policy keys on the setting app.org_id, and any routine body can change a setting,
    so the policy stops every cross-org route the catalog reads (rebinding the org variable, a second VALUES row, UPDATE .. WHERE true) but not a routine that sets
    app.org_id itself. That route is closed by rules 130 and 131, not by the policy; this test pins both facts, so that a binding the routine cannot change
    would have to flip the first assertion on purpose."""
    from .test_vetted import install
    with conn(pdb) as c:
        c.execute("DELETE FROM kt.fences")
        c.execute("INSERT INTO kt.fences (org_id, id, reason) VALUES (%s, 1, 'a'), (%s, 1, 'b')", (ORG_A, ORG_B))
        c.execute("BEGIN")
        try:
            install(c, hand("cross", f"  PERFORM set_config('app.org_id', '{ORG_B}', true);\n" + f"  DELETE FROM kt.fences t WHERE t.org_id = '{ORG_B}' AND t.id = p_id;\n  RETURN 1;",
                            pins=(PIN_FENCE,)))
            names = {r for r, _ in violations(c)}
            assert any(r.startswith(("130", "131")) for r in names), names                         # the catalog names it ...
            c.execute("SET LOCAL ROLE pipeshub_connectors")                                         # ... and the policy alone does not stop it (all inside this rolled-back transaction)
            c.execute("SELECT set_config('app.org_id', %s, true)", (ORG_A,))
            c.execute("SELECT kt.cross(1)")
            c.execute("RESET ROLE")
            assert c.execute("SELECT org_id FROM kt.fences").fetchall() == [(ORG_A,)]
        finally:
            c.execute("ROLLBACK")
            c.execute("DELETE FROM kt.fences")


def test_a_pinned_function_may_call_a_vetted_kernel_routine(pdb):
    got = planted(pdb, hand("c04", DEL_FENCE + "  PERFORM kernel.profile_epoch();\n  RETURN 1;", pins=(PIN_FENCE,)))
    assert not [r for r, _ in got if r.startswith("132")], got


def test_the_new_table_is_clean_once_it_asks_for_the_policy_and_a_table_that_reads_unbound_is_not_asked(pdb):
    assert not any(r.startswith("128") for r, _ in planted(pdb, NEW_TABLE + "SELECT kernel.require_vetted_org_policy('kt.newacc');"))
    with conn(pdb) as c:        # kt.notes is read unbound by its vetted role (a different kind of table): not what 128 is about
        assert not any(r.startswith("128") for r, _ in violations(c))


def test_a_helper_nothing_vetted_calls_is_not_read_by_the_routine_rules(pdb):
    """Rules 111, 112, 130 and 131 are about what a vetted role can run. A helper outside that reach, with the constructs they refuse, is not theirs."""
    helpers = ("CREATE FUNCTION public.h_set() RETURNS void LANGUAGE plpgsql AS 'BEGIN SET LOCAL statement_timeout = 5; END'; "
               "CREATE FUNCTION public.h_analyze() RETURNS void LANGUAGE plpgsql AS 'BEGIN ANALYZE; END'; "
               "CREATE FUNCTION public.h_temp() RETURNS void LANGUAGE plpgsql AS 'BEGIN CREATE TEMP TABLE h_t (a int); END'; "
               "CREATE FUNCTION public.h_cfg() RETURNS text LANGUAGE sql AS $$ SELECT set_config('statement_timeout', '5', true) $$; "
               "CREATE FUNCTION public.h_ret(a int) RETURNS int LANGUAGE sql RETURN a + 1; ")
    got = planted(pdb, helpers)
    assert not [r for r, _ in got if r[:3] in ("111", "112", "130", "131")], got


def test_a_helper_a_vetted_function_calls_is_read_however_deep(pdb):
    chain = ("CREATE FUNCTION public.h_deep() RETURNS void LANGUAGE plpgsql AS 'BEGIN SET LOCAL statement_timeout = 5; END'; "
                     "CREATE FUNCTION public.h_mid() RETURNS void LANGUAGE sql AS 'SELECT public.h_deep()'; ")
    got = planted(pdb, chain + hand("c05", "  PERFORM public.h_mid();\n  RETURN 1;", writer="pipeshub_query"))
    assert any(r.startswith("130") for r, _ in got), got


def test_a_routine_with_its_own_org_setting_that_a_pinned_function_calls_is_named_and_would_cross_the_policy(pdb):
    """The review's probe: an invoker routine with SET app.org_id = <another org> deletes that org's rows when a pinned function calls it, and the per-org policy
    (which keys on that setting) does not stop it. The catalog names it (132 for the call, 133 for the setting) before it can be installed through the migration."""
    from .test_vetted import install
    with conn(pdb) as c:
        c.execute("DELETE FROM kt.fences")
        c.execute("INSERT INTO kt.fences (org_id, id, reason) VALUES (%s, 7, 'a'), (%s, 7, 'b')", (ORG_A, ORG_B))
        c.execute("BEGIN")
        try:
            c.execute(f"CREATE FUNCTION public.evil_del() RETURNS void LANGUAGE sql SET app.org_id = '{ORG_B}' AS 'DELETE FROM kt.fences'")
            install(c, hand("probe", DEL_FENCE + "  PERFORM public.evil_del();\n  RETURN 1;", pins=(PIN_FENCE,)))
            names = {r for r, _ in violations(c)}
            assert any(r.startswith("132") for r in names) and any(r.startswith("133") for r in names), names
            c.execute("SET LOCAL ROLE pipeshub_connectors")                      # what the rules prevent: run as the service for org A, org B's row goes too
            c.execute("SELECT set_config('app.org_id', %s, true)", (ORG_A,))
            c.execute("SELECT kt.probe(7)")
            c.execute("RESET ROLE")
            assert c.execute("SELECT count(*) FROM kt.fences").fetchone()[0] == 0
        finally:
            c.execute("ROLLBACK")
            c.execute("DELETE FROM kt.fences")
