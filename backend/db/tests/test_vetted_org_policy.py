"""The per-org row policy for the vetted writer role (kernel_0017): the database refuses a cross-org write or read by the vetted role
whatever a function's body says, every pinned table must have it (118), and the checks (118, 119) see a table that lost it.

Runs on the fixture of test_vetted_pins (schema kt, the pinned test functions). A module that adds access tables asks for the policy the same way and is tested on its own tables."""
# ruff: noqa: F811
import pytest
from psycopg import errors as pg

from db.lib import vetted

from .rls_fixture import ORG_A, ORG_B
from .test_vetted import install
from .test_vetted_pins import (  # noqa: F401  (pdb is the module fixture)
    FNS,
    SIG,
    conn,
    mutated,
    pdb,
)

KT_PINNED = ("grants", "fences", "keys", "prefs", "changes")


def policy_violations(c) -> list[tuple[str, str]]:
    return [tuple(r) for r in c.execute("SELECT rule, object FROM kernel.org_policy_violations ORDER BY 1, 2")]


def planted(cluster, sql: str, full: bool = False) -> list[tuple[str, str]]:
    with conn(cluster) as c:
        c.execute("BEGIN")
        try:
            c.execute(sql)
            view = "kernel.rls_violations" if full else "kernel.org_policy_violations"
            return [tuple(r) for r in c.execute(f"SELECT rule, object FROM {view} ORDER BY 1, 2")]
        finally:
            c.execute("ROLLBACK")


def test_the_catalog_is_clean_with_the_policy_on_every_pinned_table(pdb):
    with conn(pdb) as c:
        assert [tuple(r) for r in c.execute("SELECT rule, object FROM kernel.rls_violations")] == []
        assert vetted.problems(c, FNS) == []
        flagged = {r[0] for r in c.execute("SELECT rel::text FROM kernel.rls_registry WHERE vetted_org_policy")}
        assert flagged == {f"kt.{t}" for t in KT_PINNED}
        pinned = {r[0] for r in c.execute("SELECT DISTINCT rel::text FROM kernel.vetted_write_pins")}
        assert pinned <= flagged


@pytest.mark.parametrize("table", [f"kt.{t}" for t in KT_PINNED])
def test_every_relation_of_a_policy_table_has_exactly_the_org_predicate_for_its_vetted_role(pdb, table):
    with conn(pdb) as c:
        role = f"pipeshub_vetted_{table.split('.')[0]}"
        leaves = [r[0] for r in c.execute("SELECT relid::text FROM pg_partition_tree(%s::regclass) UNION SELECT %s", (table, table))]
        assert len(leaves) == {"kt.grants": 6}.get(table, 1), (table, len(leaves))
        for leaf in leaves:
            pols = c.execute("SELECT polname, polcmd, pg_get_expr(polqual, polrelid), pg_get_expr(polwithcheck, polrelid), (SELECT array_agg(rolname) FROM pg_roles WHERE oid = ANY (polroles)) "
                             "FROM pg_policy WHERE polrelid = %s::regclass AND (polname LIKE 'vetted%%')", (leaf,)).fetchall()
            assert [(p[0], p[1], p[4]) for p in pols] == [("vetted_write", "*", [role])], (leaf, pols)
            assert "app.org_id" in pols[0][2] and pols[0][2] == pols[0][3] and "true" != pols[0][2], pols


# ---- the checks ---------------------------------------------------------------------------------------------------------------------

PLANTS = [
    ("118 a pinned table loses the flag", "UPDATE kernel.rls_registry SET vetted_org_policy = false WHERE rel = 'kt.grants'::regclass", "118"),
    ("119 the policy of a leaf is dropped", "DROP POLICY vetted_write ON kt.grants_h2", "119 vetted writer policy is not the per-org predicate"),
    ("119 the policy of the root is the old one", "ALTER POLICY vetted_write ON kt.grants USING (true)", "119 vetted writer policy is not the per-org predicate"),
    ("119 the policy checks nothing on write", "ALTER POLICY vetted_write ON kt.fences WITH CHECK (true)", "119 vetted writer policy is not the per-org predicate"),
    ("119 the policy is for a read only", "DROP POLICY vetted_write ON kt.keys; CREATE POLICY vetted_write ON kt.keys FOR SELECT TO pipeshub_vetted_kt USING (org_id = NULLIF(current_setting('app.org_id', true), ''))",
     "119 vetted writer policy is not the per-org predicate"),
    ("119 the policy is for another role", "ALTER POLICY vetted_write ON kt.prefs TO pipeshub_vetted_ku", "119 vetted writer policy is not the per-org predicate"),
    ("119 an unbound vetted read policy beside it", "CREATE POLICY vetted_read ON kt.grants FOR SELECT TO pipeshub_vetted_kt USING (true)", "119 vetted role has another policy"),
    ("119 a policy for PUBLIC beside it", "CREATE POLICY open ON kt.grants_h1 TO PUBLIC USING (true)", "119 vetted role has another policy"),
    ("119 a policy for another vetted role beside it", "CREATE POLICY other ON kt.changes FOR SELECT TO pipeshub_vetted_ku USING (true)", "119 vetted role has another policy"),
]


@pytest.mark.parametrize("plant", PLANTS, ids=[p[0] for p in PLANTS])
def test_a_planted_violation_is_reported(pdb, plant):
    _, sql, rule = plant
    got = planted(pdb, sql)
    assert any(r.startswith(rule) for r, _ in got), got


def test_a_table_that_lost_the_flag_is_seen_by_the_whole_catalog_view_and_not_waived_by_the_old_template_rule(pdb):
    got = planted(pdb, "UPDATE kernel.rls_registry SET vetted_org_policy = false WHERE rel = 'kt.grants'::regclass", full=True)
    rules = {r for r, _ in got}
    assert any(r.startswith("118") for r in rules) and "05 policy differs from template vetted_write" in rules, rules
    assert "05 policy differs from template vetted_write" not in {r for r, _ in planted(pdb, "SELECT 1", full=True)}


REFUSED = [
    ("a table with an unbound vetted read policy", "SELECT kernel.require_vetted_org_policy('kt.notes')"),
    ("a table without vetted_write", "SELECT kernel.require_vetted_org_policy('kt.secrets')"),
    ("a global table", "SELECT kernel.require_vetted_org_policy('kt.tokens')"),
    ("a table nobody registered", "CREATE TABLE kt.nobody (org_id text NOT NULL); SELECT kernel.require_vetted_org_policy('kt.nobody')"),
    ("a lineage table", "CREATE TABLE kt.cfg (org_id text NOT NULL, id bigint NOT NULL, PRIMARY KEY (org_id, id)); "
                        "SELECT kernel.apply_rls('kt.cfg', 'lineage', p_vetted_write => true); SELECT kernel.require_vetted_org_policy('kt.cfg')"),
]


@pytest.mark.parametrize("case", REFUSED, ids=[r[0] for r in REFUSED])
def test_require_vetted_org_policy_refuses(pdb, case):
    with conn(pdb) as c:
        c.execute("BEGIN")
        try:
            c.execute("SET LOCAL ROLE pipeshub_owner")
            # the guard's own error: a missing table (UndefinedTable) would mean the case never reached the guard
            with pytest.raises(pg.RaiseException, match="is not a registered org table"):
                for st in case[1].split("; "):
                    c.execute(st)
        finally:
            c.execute("ROLLBACK")


def test_the_registry_refuses_a_flag_on_a_table_that_reads_unbound(pdb):
    with conn(pdb) as c:
        c.execute("BEGIN")
        try:
            with pytest.raises(pg.CheckViolation):
                c.execute("UPDATE kernel.rls_registry SET vetted_org_policy = true WHERE rel = 'kt.notes'::regclass")
        finally:
            c.execute("ROLLBACK")


def test_only_the_owner_can_require_the_policy(pdb):
    with conn(pdb) as c:
        for role in ("pipeshub_connectors", "pipeshub_node", "pipeshub_vetted_kt", "pipeshub_jobs"):
            c.execute("BEGIN")
            try:
                c.execute(f"SET LOCAL ROLE {role}")
                with pytest.raises(pg.InsufficientPrivilege):
                    c.execute("SELECT kernel.require_vetted_org_policy('kt.grants')")
            finally:
                c.execute("ROLLBACK")


def test_applying_rls_again_keeps_the_policy_and_a_new_leaf_gets_it(pdb):
    with conn(pdb) as c:
        c.execute("BEGIN")
        try:
            c.execute("SET LOCAL ROLE pipeshub_owner")
            c.execute("SELECT kernel.apply_rls('kt.grants', 'org', p_vetted_write => true)")        # drift repair
            assert policy_violations(c) == []
            c.execute("CREATE TABLE kt.grants_orgx (LIKE kt.grants INCLUDING ALL)")
            c.execute("SELECT kernel.apply_rls_partition('kt.grants_orgx', 'kt.grants')")
            c.execute(f"ALTER TABLE kt.grants ATTACH PARTITION kt.grants_orgx FOR VALUES IN ('{'c' * 24}')")
            assert policy_violations(c) == []
            assert c.execute("SELECT pg_get_expr(polqual, polrelid) FROM pg_policy WHERE polrelid = 'kt.grants_orgx'::regclass AND polname = 'vetted_write'").fetchone()[0].count("app.org_id") == 1
            with pytest.raises(pg.CheckViolation):
                c.execute("SAVEPOINT s")
                c.execute("SELECT kernel.apply_rls('kt.grants', 'org', p_vetted => true, p_vetted_write => true)")      # an unbound read beside the bound write: refused by the registry
        finally:
            c.execute("ROLLBACK")


# ---- what the database does, whatever a function says ----------------------------------------------------------------------------------

def as_vetted(c, role: str, org: str):
    c.execute("BEGIN")
    c.execute(f"SET LOCAL ROLE {role}")
    c.execute("SELECT set_config('app.org_id', %s, true)", (org,))


@pytest.mark.parametrize("schema, table, row", [
    ("kt", "grants", "(org_id, principal_sk, node_sk, gtype, role) VALUES (%s, 1, 2, 1, 1)"),
    ("kt", "fences", "(org_id, id, reason) VALUES (%s, 1, 'r')"),
])
def test_the_vetted_role_sees_and_writes_only_the_org_of_the_session(pdb, schema, table, row):
    role, fq = f"pipeshub_vetted_{schema}", f"{schema}.{table}"
    with conn(pdb) as c:
        c.execute(f"DELETE FROM {fq}")
        c.execute(f"INSERT INTO {fq} {row}", (ORG_B,))
        c.execute(f"INSERT INTO {fq} {row}", (ORG_A,))
        try:
            as_vetted(c, role, ORG_A)
            assert c.execute(f"SELECT count(*) FROM {fq}").fetchone()[0] == 1                      # the other org's row is not there to read
            assert c.execute(f"SELECT count(*) FROM {fq} WHERE org_id = %s", (ORG_B,)).fetchone()[0] == 0
            c.execute("SAVEPOINT s")
            with pytest.raises(pg.InsufficientPrivilege):
                c.execute(f"INSERT INTO {fq} {row}", (ORG_B,))                                      # WITH CHECK: a row of another org
            c.execute("ROLLBACK TO SAVEPOINT s")
            assert c.execute(f"UPDATE {fq} SET org_id = org_id").rowcount == 1                       # an update reaches the session's org only
            c.execute("COMMIT")
            assert c.execute(f"SELECT count(*) FROM {fq}").fetchone()[0] == 2
        finally:
            c.execute("ROLLBACK")
            c.execute(f"DELETE FROM {fq}")


def test_a_writer_function_with_a_wrong_body_still_cannot_reach_another_org(pdb):
    """The kt writers with the org predicate taken out, and one that writes an org it names: run as the connectors service for org A."""
    leaks = [
        ("kt.w_grants_delete", "WHERE t.org_id = v_org AND t.principal_sk", "WHERE t.principal_sk"),                         # deletes whatever matches, in any org
        ("kt.w_fence_remove", "t.org_id = v_org AND ", ""),
        ("kt.w_fence_reason", "WHERE t.org_id = v_org AND t.id = p_id", "WHERE t.id = p_id"),                              # updates any org's fence
    ]
    with conn(pdb) as c:
        c.execute("DELETE FROM kt.grants; DELETE FROM kt.fences")
        for org in (ORG_A, ORG_B):
            c.execute("INSERT INTO kt.grants (org_id, principal_sk, node_sk, gtype, role) VALUES (%s, 1, 2, 1, 1)", (org,))
            c.execute("INSERT INTO kt.fences (org_id, id, reason) VALUES (%s, 1, 'orig')", (org,))
        for fn, old, new in leaks:
            c.execute("BEGIN")
            try:
                install(c, mutated(fn, old, new))
                c.execute("SET LOCAL ROLE pipeshub_connectors")
                c.execute("SELECT set_config('app.org_id', %s, true)", (ORG_A,))
                if fn == "kt.w_grants_delete":
                    got = c.execute("SELECT kt.w_grants_delete(%s, %s, %s)", ([1], [2], [1])).fetchone()[0]
                elif fn == "kt.w_fence_remove":
                    got = c.execute("SELECT kt.w_fence_remove(%s)", (1,)).fetchone()[0]
                else:
                    got = c.execute("SELECT kt.w_fence_reason(%s, %s)", (1, "changed")).fetchone()[0]
                assert got in (1, True), (fn, got)                                                  # it did its own org's row ...
                c.execute("COMMIT")
            except BaseException:
                c.execute("ROLLBACK")
                raise
            # ... and nothing of org B's
            assert c.execute("SELECT count(*) FROM kt.grants WHERE org_id = %s", (ORG_B,)).fetchone()[0] == 1, fn
            assert c.execute("SELECT reason FROM kt.fences WHERE org_id = %s", (ORG_B,)).fetchone()[0] == "orig", fn
            c.execute("INSERT INTO kt.grants (org_id, principal_sk, node_sk, gtype, role) VALUES (%s, 1, 2, 1, 1) ON CONFLICT DO NOTHING", (ORG_A,))
            c.execute("INSERT INTO kt.fences (org_id, id, reason) VALUES (%s, 1, 'orig') ON CONFLICT (org_id, id) DO UPDATE SET reason = 'orig'", (ORG_A,))
            # restore the shipped bodies and the pins
            install(c, vetted.render(FNS[SIG[fn]], replace=True))
        # and a writer that names another org for its rows: refused by WITH CHECK
        c.execute("BEGIN")
        try:
            install(c, mutated("kt.w_pref_put", "VALUES (v_org, p_k, p_v)", f"VALUES ('{ORG_B}', p_k, p_v)"))
            c.execute("SET LOCAL ROLE pipeshub_connectors")
            c.execute("SELECT set_config('app.org_id', %s, true)", (ORG_A,))
            with pytest.raises(pg.InsufficientPrivilege):
                c.execute("SELECT kt.w_pref_put(%s, %s)", ("k", "v"))
        finally:
            c.execute("ROLLBACK")
        assert c.execute("SELECT count(*) FROM kt.prefs WHERE org_id = %s", (ORG_B,)).fetchone()[0] == 0
        c.execute("DELETE FROM kt.grants; DELETE FROM kt.fences")
