"""Vetted SECURITY DEFINER functions: the generator, the checklist as catalog checks with a planted violation for every item,
the generated per-function suite (no org, foreign org, own org, index use, validation, privileges, catalog, timing, shadowing),
attacks from a compromised caller, per-schema owners, kernel.org_lineage and its extension point.

The suite is driven by the manifests: kernel/vetted and platform/vetted (shipped) and vetted_manifest.yaml (schemas kt and ku)."""
import dataclasses
import os
import re
import shutil
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

import psycopg
import pytest
import yaml
from psycopg import errors as pg
from psycopg import sql as pgsql
from psycopg.types.json import Jsonb

from db import bootstrap_roles, lint, surface, vetted_harness
from db.lib import vetted

from .conftest import BACKEND, EXTENSIONS, VETTED, _drop_roles, _free_port, run_migrate
from .rls_fixture import ORG_A, ORG_B, OrgDb, start_pgbouncer, uow

CORE = BACKEND / "db" / "versions" / "kernel" / "vetted" / "manifest.yaml"
PLATFORM = BACKEND / "db" / "versions" / "platform" / "vetted" / "manifest.yaml"
IDENTITY = BACKEND / "db" / "versions" / "identity" / "vetted" / "manifest.yaml"
FIXTURE = Path(__file__).with_name("vetted_manifest.yaml")
FNS = vetted.load_all([CORE, PLATFORM, IDENTITY, FIXTURE])
ALL = list(FNS.values())
IDS = [f.qualified for f in ALL]
DB_NAME = "t_vetted"

SCHEMA_SQL = f"""
SET ROLE pipeshub_owner;
CREATE SCHEMA kt;
CREATE SCHEMA ku;
CREATE TABLE kt.notes (org_id text NOT NULL, id text NOT NULL, title text NOT NULL, body text, PRIMARY KEY (org_id, id));
CREATE INDEX notes_title_idx ON kt.notes (org_id, title, id);
SELECT kernel.apply_rls('kt.notes', 'org', p_vetted => true, p_vetted_write => true);
CREATE TABLE kt.secrets (org_id text NOT NULL, id text NOT NULL, secret text, PRIMARY KEY (org_id, id));
SELECT kernel.apply_rls('kt.secrets', 'org');                                       -- never opted into vetted reads
CREATE TABLE kt.tokens (token_hash text PRIMARY KEY, org_id text NOT NULL, user_id text NOT NULL);
SELECT kernel.apply_rls('kt.tokens', 'global', p_vetted_write => true);
CREATE TABLE ku.items (org_id text NOT NULL, id text NOT NULL, label text, PRIMARY KEY (org_id, id));
SELECT kernel.apply_rls('ku.items', 'org', p_vetted => true);
GRANT SELECT ON kt.notes, kt.secrets TO pipeshub_node;                              -- a caller may read its own org's rows directly
GRANT USAGE ON SCHEMA kt, ku TO pipeshub_app, pipeshub_jobs, pipeshub_node_login, pipeshub_jobs;
RESET ROLE;
GRANT TEMPORARY ON DATABASE {DB_NAME} TO pipeshub_node, pipeshub_node_login, pipeshub_indexing, pipeshub_indexing_login;   -- writers that COPY through temp tables (the shadowing check makes one)
INSERT INTO kt.secrets VALUES ('{ORG_B}', 's1', 'top secret of B'), ('{ORG_A}', 's1', 'secret of A');
"""


def install(admin: psycopg.Connection, sql: str) -> None:
    """Run generated function SQL the way a revision does: as the owner role, in one transaction."""
    with admin.transaction():
        admin.execute("SET LOCAL ROLE pipeshub_owner")
        admin.execute(sql)
        admin.execute("RESET ROLE")


@pytest.fixture(scope="module")
def vdb(cluster, tmp_path_factory):
    pgb = os.environ.get("MIGRATE_TEST_PGBOUNCER") or shutil.which("pgbouncer")
    if not pgb:
        pytest.skip("no pgbouncer (set MIGRATE_TEST_PGBOUNCER)")
    with cluster.admin() as admin:
        admin.execute(f"DROP DATABASE IF EXISTS {DB_NAME} WITH (FORCE)")
        _drop_roles(admin)
        admin.execute(f"CREATE DATABASE {DB_NAME}")
    with cluster.admin(DB_NAME) as admin:
        bootstrap_roles.bootstrap(admin, ["kt", "ku", *VETTED], extensions=EXTENSIONS)
    r = run_migrate(cluster.url(DB_NAME, "pipeshub_migrate"), PG_MIGRATE_SURFACE_CHECK="skip")       # the fixture adds roles and schemas
    assert r.returncode == 0, r.stdout
    with cluster.admin(DB_NAME) as admin:
        admin.execute(SCHEMA_SQL)
        for fn in ALL:
            if fn.schema in ("kt", "ku"):
                install(admin, vetted.render(fn))
        admin.execute("ANALYZE")
    proc = start_pgbouncer(pgb, cluster, DB_NAME, tmp_path_factory.mktemp("pgb"), _free_port(cluster.port + 400))
    yield OrgDb(cluster, DB_NAME, proc.port)
    proc.kill()
    proc.wait(timeout=10)
    with cluster.admin() as admin:
        admin.execute(f"DROP DATABASE IF EXISTS {DB_NAME} WITH (FORCE)")
        _drop_roles(admin)


# ---- helpers -----------------------------------------------------------------------------------------------------

def sub(v, org: str):
    if isinstance(v, str):
        return v.replace("{org}", org).replace("{long}", "x" * 1001)
    return [sub(x, org) for x in v] if isinstance(v, list) else v


def call_sql(fn: vetted.Fn, n: int) -> str:
    marks = ", ".join(["%s"] * n)
    return f"SELECT * FROM {fn.qualified}({marks})" if fn.returns_set else f"SELECT {fn.qualified}({marks})"


def rows(cur) -> list[list]:
    return [list(r) for r in cur.fetchall()]


def role_of(fn: vetted.Fn) -> str:
    return fn.writer or "pipeshub_node"


def invoke(t: OrgDb, fn: vetted.Fn, as_org: str | None, args: list, role: str | None = None) -> list[list]:
    """What a service does: one pooled transaction, role and org set, then the call."""
    role = role or role_of(fn)
    notes: list[str] = []
    with t.pooled(role.removeprefix("pipeshub_")) as c, uow(c, role, as_org, user=fn.test.get("user")):
        c.add_notice_handler(lambda d: notes.append(d.message_primary or ""))
        got = rows(c.execute(call_sql(fn, len(args)), args))
    assert not notes, f"{fn.qualified} sent notices to the caller: {notes}"          # a notice is a side channel for values of other orgs
    return got


def seed(t: OrgDb, fn: vetted.Fn, org: str = ORG_B) -> None:
    with t.admin() as c:
        if sql := fn.test.get("needle"):
            c.execute(sub(sql, org))


def volume(t: OrgDb, fn: vetted.Fn) -> None:
    with t.admin() as c:
        if sql := fn.test.get("volume"):
            for org in (ORG_A, ORG_B):
                c.execute(sub(sql, org))
            c.execute("ANALYZE")


def state(t: OrgDb, fn: vetted.Fn):
    if not (sql := fn.test.get("state")):
        return None
    with t.admin() as c:
        return rows(c.execute(sub(sql, ORG_B)))


def sample_args(fn: vetted.Fn, org: str = ORG_B) -> list:
    return sub(fn.test.get("args", []), org)


# ---- the generated suite, one function per check; each returns the problems it found --------------------------------------

def check_no_org(t, fn) -> list[str]:
    seed(t, fn)
    want = sub(fn.test.get("no_org_result", []), ORG_B)
    return [f"no org -> {got}" for org in (None, "") if (got := invoke(t, fn, org, sample_args(fn))) != want]


def check_foreign_org(t, fn) -> list[str]:
    seed(t, fn)
    before = state(t, fn)
    got = invoke(t, fn, ORG_A, sample_args(fn))
    out = [] if got == sub(fn.test.get("other_result", []), ORG_B) else [f"org A got {got}"]
    return out + ([] if state(t, fn) == before else ["org A changed org B's row"])


def check_own_org(t, fn) -> list[str]:
    seed(t, fn)
    got = invoke(t, fn, ORG_B, sample_args(fn))
    return [] if got == sub(fn.test["own_result"], ORG_B) else [f"org B got {got}"]


def check_plan(t, fn, mode="auto_explain") -> list[str]:
    seed(t, fn)
    volume(t, fn)
    args = sample_args(fn)
    with t.admin() as c:
        return vetted_harness.check_plans(c, fn, ORG_B, call_sql(fn, len(args)), args, role_of(fn), fn.test.get("index"), mode, user=fn.test.get("user"))


def check_validation(t, fn) -> list[str]:
    out = []
    for bad in fn.test["bad_args"]:
        try:
            invoke(t, fn, ORG_B, sub(bad, ORG_B))
            out.append(f"{bad} was accepted")
        except pg.InvalidParameterValue:
            pass
        except psycopg.Error as e:
            out.append(f"{bad} -> {type(e).__name__} (expected 22023)")
    return out


def check_privileges(t, fn) -> list[str]:
    out = []
    args = sample_args(fn) or [None] * len(fn.args)
    denied = (pg.InsufficientPrivilege,)

    def attempt(who: str, run) -> None:
        try:
            run()
            out.append(f"{who} could call it")
        except denied:
            pass

    with t.admin() as c:                                             # roles that must never run it: jobs, backup, restore, other services
        # a jobs-class function is the one only the jobs role may run: every service role is refused instead
        never = ("pipeshub_backup", "pipeshub_restore", *(["pipeshub_node", "pipeshub_connectors", "pipeshub_indexing", "pipeshub_query"] if fn.cls == "jobs"
                                                          else ["pipeshub_jobs", *(["pipeshub_connectors"] if fn.cls == "write" else [])]))
        for role in never:
            def run(role=role):
                with c.transaction(force_rollback=True):
                    c.execute(pgsql.SQL("SET LOCAL ROLE {}").format(pgsql.Identifier(role)))
                    c.execute(call_sql(fn, len(args)), args)
            attempt(role, run)
    with t.pooled("node") as c:                                      # the login itself, before the unit of work's role switch
        attempt("node login without a role switch", lambda: c.execute(call_sql(fn, len(args)), args))
    if fn.scope == "helper":
        attempt("node service role", lambda: invoke(t, fn, ORG_B, args))
    return out


def check_catalog(t, fn) -> list[str]:
    with t.admin() as c:
        bad = list(c.execute("SELECT rule, object FROM kernel.rls_violations WHERE object LIKE %s", (f"{fn.qualified}%",)))
        bad += [p for p in vetted.problems(c, FNS) if fn.qualified in p[1]]
    return [str(b) for b in bad]


def check_timing(t, fn) -> list[str]:
    seed(t, fn)
    volume(t, fn)
    args = sample_args(fn)
    ms = []
    with t.pooled(role_of(fn).removeprefix("pipeshub_")) as c, uow(c, role_of(fn), ORG_B, user=fn.test.get("user")):
        for _ in range(20):
            t0 = time.perf_counter()
            c.execute(call_sql(fn, len(args)), args).fetchall()
            ms.append((time.perf_counter() - t0) * 1000)
    p95 = statistics.quantiles(ms, n=20)[-1]
    return [] if p95 <= fn.test.get("max_ms", 250) else [f"p95 {p95:.0f} ms"]


def forged(column_type: str, key: str):
    """A value for a column of the forged row: the key where the column holds text, a plain value of its type otherwise."""
    ty = column_type.lower()
    if ty.endswith("[]"):
        return [key] if ty.startswith(("text", "character")) else None
    if "vector" in ty or "halfvec" in ty or ty.startswith("bit") or ty == "uuid":
        return None             # no plain value of these types fills a key
    if ty in ("integer", "bigint", "smallint", "real", "double precision"):
        return 0
    if ty == "boolean":
        return False
    if ty.startswith("timestamp"):
        return datetime.now(timezone.utc)
    if ty in ("jsonb", "json"):
        return Jsonb([])
    if ty.endswith(".oid24"):
        return "0" * 24
    return key


def check_shadowing(t, fn) -> list[str]:
    """The caller (with TEMP) makes a temp table named like each table the function reads, holding a forged row that matches."""
    seed(t, fn)
    args = sample_args(fn)
    key = next((a for a in args if isinstance(a, str)), "x")
    want = invoke(t, fn, ORG_B, args)
    out = []
    for table in fn.reads:
        with t.admin() as a:
            cols = a.execute("SELECT attname, format_type(atttypid, atttypmod) FROM pg_attribute WHERE attrelid = %s::regclass AND attnum > 0 "
                             "AND NOT attisdropped ORDER BY attnum", (table,)).fetchall()
        short = table.split(".")[1]
        ddl = ", ".join(f'"{n}" {ty}' for n, ty in cols)
        vals = [ORG_B if n == "org_id" else forged(ty, key) for n, ty in cols]
        role = role_of(fn)
        # a fresh backend, not a pooled one: a plan cached by an earlier call would hide the shadowing
        with psycopg.connect(host="127.0.0.1", port=t.cluster.port, user=f"{role}_login", dbname=t.name) as c, uow(c, role, ORG_B, user=fn.test.get("user")):
            c.execute(f'CREATE TEMP TABLE "{short}" ({ddl}) ON COMMIT DROP')
            c.execute(f'INSERT INTO pg_temp."{short}" VALUES ({", ".join(["%s"] * len(cols))})', vals)
            c.execute(f'GRANT ALL ON pg_temp."{short}" TO PUBLIC')           # the attacker owns it: the vetted owner may read it too
            got = rows(c.execute(call_sql(fn, len(args)), args))
        if got != want:
            out.append(f"a temp table named {short} changed the result: {got}")
    return out


def applicable(fn: vetted.Fn) -> dict[str, bool]:
    t = fn.test
    if "checks" in t:
        return {v: v in t["checks"] for v in ("no_org", "foreign_org", "own_org", "plan", "validation", "privileges", "catalog", "timing", "shadowing")}
    return {"no_org": "args" in t, "foreign_org": "args" in t, "own_org": "own_result" in t, "plan": bool(fn.reads) and "args" in t, "validation": "bad_args" in t, "privileges": True,
            "catalog": True, "timing": "max_ms" in t, "shadowing": bool(fn.reads) and "args" in t}


SUITE = {"no_org": check_no_org, "foreign_org": check_foreign_org, "own_org": check_own_org, "plan": check_plan, "validation": check_validation, "privileges": check_privileges,
         "catalog": check_catalog, "timing": check_timing, "shadowing": check_shadowing}


@pytest.mark.parametrize("fn", ALL, ids=IDS)
@pytest.mark.parametrize("check", list(SUITE))
def test_generated_suite(vdb, fn, check):
    if not applicable(fn)[check]:
        pytest.skip(f"{check} does not apply to {fn.qualified}")
    assert SUITE[check](vdb, fn) == []


@pytest.mark.parametrize("fn", ALL, ids=IDS)
def test_every_function_in_a_manifest_declares_its_tests(fn):
    assert fn.test, f"{fn.qualified} has no test section"
    a = applicable(fn)
    assert a["privileges"] and a["catalog"]
    if fn.scope in ("org", "lineage"):
        assert a["no_org"] and a["foreign_org"] and a["own_org"] and a["validation"], "an org-scoped function needs args, own_result and bad_args"
    if fn.reads and fn.cls != "jobs":         # a jobs-class function cannot be run by a service role; its path is pg_temp-last by checks 16 and 58
        assert a["shadowing"], "a function that reads tables is checked for shadowing"


def test_the_managed_fallback_gives_the_same_answer_on_the_index(vdb):
    fn = FNS["kt.notes_by_title(text, int)"]
    assert check_plan(vdb, fn, mode="explain") == []


def test_auto_explain_sees_the_plans_of_the_statements_inside_the_function(vdb):
    fn = FNS["kt.notes_by_title(text, int)"]
    seed(vdb, fn)
    volume(vdb, fn)
    with vdb.admin() as c:
        plans = vetted_harness.plans_auto_explain(c, "pipeshub_node", ORG_B, call_sql(fn, 2), ("findme", 5))
    assert len(plans) == 7 and all("notes_title_idx" in p and "Query Text" in p for p in plans)


def test_the_plan_gate_fails_a_function_that_scans_a_big_org_table_or_misses_its_index(vdb):
    fn = FNS["kt.notes_by_title(text, int)"]
    slow = dataclasses.replace(fn, name="notes_by_lower", body=fn.body.replace("n.title = p_title", "lower(n.title) = lower(p_title)"))
    volume(vdb, fn)
    with vdb.admin() as c, c.transaction(force_rollback=True):
        # the planner is told not to use indexes (a stand-in for a body whose predicate no index can serve)
        install(c, vetted.render(slow).replace("SET search_path = pg_catalog, kt, pg_temp", "SET search_path = pg_catalog, kt, pg_temp\nSET enable_indexscan = off\nSET enable_bitmapscan = off"))
        args = ["findme", 5]
        got = vetted_harness.check_plans(c, slow, ORG_B, call_sql(slow, 2), args, "pipeshub_node", "notes_title_idx", "auto_explain")
        c.execute("SET LOCAL enable_indexscan = off")
        c.execute("SET LOCAL enable_bitmapscan = off")        # EXPLAIN runs the bare query: give it the settings the function carries
        fallback = vetted_harness.check_plans(c, slow, ORG_B, call_sql(slow, 2), args, "pipeshub_node", "notes_title_idx", "explain")
        threshold = vetted_harness.check_plans(c, slow, ORG_B, call_sql(slow, 2), args, "pipeshub_node", None, "auto_explain", min_rows=10**9)
    assert [g for g in got if "sequentially" in g] and all(f"call {i}" in " ".join(got) for i in range(1, 8)), got           # every call
    assert "call 1 does not use index notes_title_idx" in got and "call 7 does not use index notes_title_idx" in got
    assert any("scans notes sequentially" in f for f in fallback) and "EXPLAIN does not use index notes_title_idx" in fallback
    assert threshold == []                                                  # a table under the threshold is not a finding


def test_an_index_is_matched_by_its_whole_name():
    plan = "Index Scan using notes_title_idx2 on notes n\n  Index Cond: (org_id = 'x')"
    assert not vetted_harness.plan_uses_index(plan, "notes_title_idx")
    assert vetted_harness.plan_uses_index(plan, "notes_title_idx2")
    assert vetted_harness.plan_uses_index("Bitmap Index Scan on notes_title_idx\n", "notes_title_idx")
    assert vetted_harness.plan_uses_index("Index Only Scan using notes_title_idx on notes n", "notes_title_idx")
    assert not vetted_harness.plan_uses_index("Seq Scan on notes_title_idx_log", "notes_title_idx")
    assert vetted_harness.seq_scanned("Parallel Seq Scan on kt.notes n\nSeq Scan on small", {"notes": 5}) == ["notes"]


def test_the_shadowing_check_catches_a_function_without_pg_temp_last(vdb):
    """Mutation: unqualified table, no pg_temp in the path (so PostgreSQL searches it first). the shadowing check must fail and the catalog must say why."""
    fn = FNS["kt.notes_by_title(text, int)"]
    bad = dataclasses.replace(fn, name="notes_shadowed")
    sql = vetted.render(bad).replace("SET search_path = pg_catalog, kt, pg_temp", "SET search_path = pg_catalog, kt").replace("FROM kt.notes n", "FROM notes n")
    with vdb.admin() as c, c.transaction(force_rollback=True):
        install(c, sql)
        assert any("16 " in r for r, _ in c.execute("SELECT rule, object FROM kernel.rls_violations WHERE object LIKE 'kt.notes_shadowed%'"))
    with vdb.admin() as c:
        install(c, sql)
    try:
        res = check_shadowing(vdb, bad)
        assert any("changed the result" in p for p in res), res
    finally:
        with vdb.admin() as c:
            c.execute("DROP FUNCTION kt.notes_shadowed(text, int)")
            c.execute("DELETE FROM kernel.function_registry WHERE proc::oid NOT IN (SELECT oid FROM pg_proc)")


def test_a_write_function_that_forgets_the_org_predicate_is_caught_by_the_suite(vdb):
    fn = FNS["kt.set_note_title(text, text)"]
    leaky = dataclasses.replace(fn, name="set_note_leaky", body=fn.body.replace("org_id = v_org AND ", ""))
    with vdb.admin() as c:
        install(c, vetted.render(leaky))
    try:
        assert any("changed org B's row" in p for p in check_foreign_org(vdb, leaky))
    finally:
        with vdb.admin() as c:
            c.execute("DROP FUNCTION kt.set_note_leaky(text, text)")
            c.execute("DELETE FROM kernel.function_registry WHERE proc::oid NOT IN (SELECT oid FROM pg_proc)")
            c.execute(sub(fn.test["needle"], ORG_B))


def test_the_suite_sees_a_notice_that_carries_a_value_of_another_org(vdb):
    """The reproduction: a compliant-looking function that RAISEs NOTICE with a sub-select over every org (vetted_read is USING (true))."""
    fn = FNS["kt.notes_by_title(text, int)"]
    seed(vdb, fn)
    leaky = plant(name="kt.notice_leak", body=swap("IF v_org IS NULL", "RAISE NOTICE 'dbg %', (SELECT n.title FROM kt.notes n WHERE n.id = 'needle-1' LIMIT 1); IF v_org IS NULL"))
    probe = dataclasses.replace(fn, name="notice_leak", args=(fn.args[0],), test={})
    with vdb.admin() as c:
        c.execute(leaky)
    try:
        with pytest.raises(AssertionError, match="notices"):
            invoke(vdb, probe, ORG_A, ["x"])
        with vdb.admin() as c:
            assert any(r.startswith("74") for r, _ in c.execute("SELECT rule, object FROM kernel.rls_violations"))
    finally:
        with vdb.admin() as c:
            c.execute("DROP FUNCTION kt.notice_leak(text)")
            c.execute("DELETE FROM kernel.function_registry WHERE proc::oid NOT IN (SELECT oid FROM pg_proc)")


def test_a_jobs_write_unit_runs_its_prologue(vdb):
    """The jobs login calls the fence before the role switch, as the service logins do; it holds nothing else on kernel."""
    with vdb.pooled_as("pipeshub_jobs") as c, c.transaction():
        c.execute("SELECT kernel.assert_profile_epoch(1)")
        with pytest.raises(pg.ObjectNotInPrerequisiteState), c.transaction():
            c.execute("SELECT kernel.assert_profile_epoch(2)")
        with pytest.raises(pg.InsufficientPrivilege), c.transaction():
            c.execute("SELECT * FROM kernel.settings")


def test_the_catalog_and_the_manifests_agree(vdb):
    with vdb.admin() as c:
        assert c.execute("SELECT rule, object FROM kernel.rls_violations").fetchall() == []
        assert vetted.problems(c, FNS) == []


# ---- the checklist: a planted violation for every item ---------------------------------------------------------------

GOOD = """DECLARE v_org text := NULLIF(current_setting('app.org_id', true), '');
BEGIN
  IF p_x IS NULL OR length(p_x) > 64 THEN RAISE EXCEPTION 'bad argument' USING ERRCODE = '22023'; END IF;
  IF v_org IS NULL THEN RETURN; END IF;
  RETURN QUERY SELECT n.id FROM kt.notes n WHERE n.org_id = v_org AND n.title = p_x LIMIT 5;
END"""


def plant(name="kt.plant", args="p_x text", types="text", returns="TABLE (id text)", lang="plpgsql", vol="STABLE",
          path="pg_catalog, kt, pg_temp", body=GOOD, owner="pipeshub_vetted_kt", grants=("pipeshub_app",), public=False,
          reg=("read", None, "org", False), extra="") -> str:
    """SQL that creates one (violating) function the way the generated SQL would, as a superuser in a transaction."""
    sig = f"{name}({types})"
    settings = (f"SET search_path = {path} " if path else "") + "SET standard_conforming_strings = on"
    src = f"AS $b${body}$b$" if lang == "plpgsql" else f"AS {body}"
    parts = [f"CREATE FUNCTION {name}({args}) RETURNS {returns} LANGUAGE {lang} {vol} SECURITY DEFINER {settings} {src};",
             f"ALTER FUNCTION {sig} OWNER TO {owner};"]
    if not public:
        parts.append(f"REVOKE ALL ON FUNCTION {sig} FROM PUBLIC;")
    parts += [f"GRANT EXECUTE ON FUNCTION {sig} TO {g};" for g in grants]
    cls, writer, scope, content_ok = reg or (None,) * 4
    if reg:
        parts.append(f"SELECT kernel.register_function('{sig}', '{cls}', {f'{chr(39)}{writer}{chr(39)}' if writer else 'NULL'}, '{scope}', {str(content_ok).lower()});")
    return "\n".join(parts) + "\n" + extra


def swap(old: str, new: str, body: str = GOOD) -> str:
    assert old in body
    return body.replace(old, new)


WRITE_BODY = """DECLARE v_org text := NULLIF(current_setting('app.org_id', true), '');
BEGIN
  IF p_x IS NULL OR length(p_x) > 64 THEN RAISE EXCEPTION 'bad argument' USING ERRCODE = '22023'; END IF;
  IF v_org IS NULL THEN RETURN false; END IF;
  UPDATE kt.notes SET title = p_x WHERE org_id = v_org AND id = 'n1';
  RETURN FOUND;
END"""

CHECKLIST = [
    # (item, planted SQL, rule that must be reported)
    ("1 a read function is VOLATILE", plant(vol="VOLATILE"), "36 read-class function is VOLATILE"),
    ("1 a read function deletes", plant(body=swap("RETURN QUERY", "DELETE FROM kt.notes WHERE false; RETURN QUERY")), "36 read-class function body has DML"),
    ("1 a read function uses EXECUTE", plant(body=swap("RETURN QUERY SELECT n.id FROM kt.notes n WHERE n.org_id = v_org AND n.title = p_x LIMIT 5",
                                                       "RETURN QUERY EXECUTE 'SELECT id FROM kt.notes LIMIT 5'")), "56"),
    ("1 a read function builds SQL with format()", plant(body=swap("RETURN QUERY SELECT n.id FROM kt.notes n WHERE n.org_id = v_org AND n.title = p_x LIMIT 5",
                                                                   "PERFORM format('%s', p_x); RETURN QUERY SELECT n.id FROM kt.notes n WHERE n.org_id = v_org LIMIT 5")), "56"),
    ("1 a write function builds SQL with format()", plant(vol="VOLATILE", returns="boolean", reg=("write", "pipeshub_node", "org", False),
                                                          grants=("pipeshub_node",), body=swap("RETURN FOUND", "PERFORM format('%s', p_x); RETURN FOUND", WRITE_BODY)), "56"),
    ("1 a function in a language that leaves the database", plant(args="p_x integer", types="integer", returns="integer", lang="internal",
                                                                   body="'int4um'", path="pg_catalog, kt, pg_temp"), "55"),
    ("1 a write function uses ON CONFLICT", plant(vol="VOLATILE", returns="boolean", reg=("write", "pipeshub_node", "org", False), grants=("pipeshub_node",),
                                                   body=swap("UPDATE kt.notes SET title = p_x WHERE org_id = v_org AND id = 'n1';",
                                                             "INSERT INTO kt.notes (org_id, id, title) VALUES (v_org, 'n1', p_x) ON CONFLICT (org_id, id) DO UPDATE SET title = p_x;", WRITE_BODY)), "57"),
    ("2 pg_temp first in the path", plant(path="pg_temp, pg_catalog, kt"), "16 "),
    ("2 no pg_temp in the path", plant(path="pg_catalog, kt"), "16 "),
    ("2 no path at all", plant(path=""), "16 "),
    ("2 public in the path", plant(path="pg_catalog, kt, public, pg_temp"), "58 SECURITY DEFINER search_path lists public"),
    ("2 another vetted schema in the path", plant(path="pg_catalog, kt, ku, pg_temp"), "58 SECURITY DEFINER search_path lists ku"),
    ("3 the org is never read", plant(body=swap("v_org text := NULLIF(current_setting('app.org_id', true), '')", "v_org text := 'x'")), "59"),
    ("3 the org is an argument", plant(args="p_org_id text", types="text", body=swap("p_x", "p_org_id")
                                       .replace("v_org text := NULLIF(current_setting('app.org_id', true), '')", "v_org text := p_org_id")), "60"),
    ("4 an argument is not validated", plant(body=swap("IF p_x IS NULL OR length(p_x) > 64 THEN RAISE EXCEPTION 'bad argument' USING ERRCODE = '22023'; END IF;", "")), "62"),
    ("4 errors use another SQLSTATE", plant(body=swap("'22023'", "'P0001'")), "62"),
    ("5 a whole table row is returned", plant(returns="SETOF kt.notes", body=swap("SELECT n.id FROM", "SELECT n.* FROM")), "63"),
    ("5 a content column is returned", plant(returns="TABLE (id text, body text)", body=swap("SELECT n.id FROM", "SELECT n.id, n.body FROM")), "64"),
    ("6 no LIMIT", plant(body=swap(" LIMIT 5", "")), "65"),
    ("7 owned by the owner role", plant(owner="pipeshub_owner"), "15"),
    ("7 executable by PUBLIC", plant(public=True), "17"),
    ("7 executable by the jobs role", plant(grants=("pipeshub_app", "pipeshub_jobs")), "66"),
    ("7 executable by the backup role", plant(grants=("pipeshub_app", "pipeshub_backup")), "66"),
    ("8 the vetted role may read a table that did not opt in", "GRANT SELECT ON kt.secrets TO pipeshub_vetted_kt", "73"),
    ("8 the vetted role may write a table that did not opt in", "GRANT UPDATE ON kt.secrets TO pipeshub_vetted_kt", "73"),
    ("8 the vetted role holds a grant in another schema", "GRANT SELECT ON ku.items TO pipeshub_vetted_kt", "42"),
    ("11 a cast on row data", plant(body=swap("n.title = p_x", "n.title::integer = 1")), "67"),
    ("11 a division", plant(body=swap("LIMIT 5", "LIMIT 10 / 2")), "67"),
    ("11 date parsing of stored text", plant(body=swap("n.title = p_x", "to_date(n.title, 'YYYY') IS NOT NULL")), "67"),
    ("11 a jsonb path", plant(body=swap("n.title = p_x", "jsonb_path_exists(n.title::jsonb, '$')")), "67"),
    ("3 a global lookup that matches by pattern", plant(reg=("read", None, "global", False), body=swap("n.title = p_x", "n.title ILIKE p_x")), "61"),
    ("3 a lineage function that never reads the org", plant(reg=("read", None, "lineage", False), body=swap("v_org text := NULLIF(current_setting('app.org_id', true), '')", "v_org text := 'x'")), "59"),
    ("a helper that others can execute", plant(reg=("read", None, "helper", False)), "72"),
    # side channels: RAISE is only a constant EXCEPTION with an SQLSTATE
    ("1 a notice carries a value from a sub-select", plant(body=swap("IF v_org IS NULL", "RAISE NOTICE 'dbg %', (SELECT n.title FROM kt.notes n LIMIT 1); IF v_org IS NULL")), "74"),
    ("1 a notice with a constant text", plant(body=swap("IF v_org IS NULL", "RAISE NOTICE 'hello'; IF v_org IS NULL")), "74"),
    ("1 a warning", plant(body=swap("IF v_org IS NULL", "RAISE WARNING 'w' USING ERRCODE = '22023'; IF v_org IS NULL")), "74"),
    ("1 an exception message with a format argument", plant(body=swap("IF v_org IS NULL", "RAISE EXCEPTION 'no %', p_x USING ERRCODE = '22023'; IF v_org IS NULL")), "74"),
    ("1 an exception with DETAIL", plant(body=swap("IF v_org IS NULL", "RAISE EXCEPTION 'no' USING ERRCODE = '22023', DETAIL = (SELECT n.title FROM kt.notes n LIMIT 1); IF v_org IS NULL")), "74"),
    ("1 an exception without an SQLSTATE", plant(body=swap("IF v_org IS NULL", "RAISE EXCEPTION 'no'; IF v_org IS NULL")), "74"),
    ("1 ASSERT with a message", plant(body=swap("IF v_org IS NULL", "ASSERT p_x IS NOT NULL, (SELECT n.title FROM kt.notes n LIMIT 1); IF v_org IS NULL")), "74"),
    # spellings the first regexes missed
    ("1 query_to_xml runs a string as SQL", plant(body=swap("IF v_org IS NULL", "PERFORM query_to_xml('select 1', true, false, ''); IF v_org IS NULL")), "56"),
    ("3 the org is an argument under another name", plant(args="p_account text", types="text", body=swap("n.title = p_x", "n.title = p_account").replace("p_x", "p_account")), "60"),
    ("3 the org column is compared with an argument", plant(body=swap("n.org_id = v_org AND n.title = p_x", "n.org_id = p_x")), "60"),
    ("5 whole rows as json", plant(returns="TABLE (id text, j jsonb)", body=swap("SELECT n.id FROM", "SELECT n.id, to_jsonb(n) FROM")), "63"),
    ("5 a json return", plant(returns="jsonb", body="BEGIN RETURN '{}'; END", vol="STABLE").replace("DECLARE v_org text := NULLIF(current_setting('app.org_id', true), '');", ""), "63"),
    ("11 a cast to int4", plant(body=swap("n.title = p_x", "n.title::int4 = 1")), "67"),
    ("11 a schema-qualified cast", plant(body=swap("n.title = p_x", "n.title::pg_catalog.int8 = 1")), "67"),
    ("11 a function-style cast", plant(body=swap("n.title = p_x", "int4(n.title) = 1")), "67"),
    ("11 a division without spaces", plant(body=swap("LIMIT 5", "LIMIT 10/2")), "67"),
    ("11 a modulo", plant(body=swap("LIMIT 5", "LIMIT 10 % 3")), "67"),
    ("a helper executable by another vetted role", plant(reg=("read", None, "helper", False), grants=("pipeshub_vetted_ku",)), "72 helper function executable by pipeshub_vetted_ku"),
    ("a vetted-owner read outside a declared extension schema", "INSERT INTO kernel.vetted_extension_reads VALUES ('kt.tokens'::regclass)", "75"),
    ("the lineage extension point loses its shape",
     "DROP FUNCTION kernel.org_parent_ids(text); CREATE FUNCTION kernel.org_parent_ids(p_org text) RETURNS text LANGUAGE sql STABLE SECURITY DEFINER "
     "SET search_path = pg_catalog, kernel, pg_temp AS $$ SELECT 'x' $$; ALTER FUNCTION kernel.org_parent_ids(text) OWNER TO pipeshub_vetted_kernel; "
     "SELECT kernel.register_function('kernel.org_parent_ids(text)', 'read', NULL, 'helper', false)", "68 kernel.org_parent_ids"),
    ("the lineage function is registered with another scope", "UPDATE kernel.function_registry SET scope = 'org' WHERE proc = 'kernel.org_lineage(text)'::regprocedure", "68 kernel.org_lineage"),
    ("the lineage function becomes SECURITY INVOKER", "ALTER FUNCTION kernel.org_lineage(text) SECURITY INVOKER", "68 kernel.org_lineage"),
    ("a built-in function runs with its owner's rights", "UPDATE pg_proc SET prosecdef = true WHERE proname = 'texteq'", "70"),
    ("an unregistered SECURITY DEFINER function", plant(reg=None), "35"),
]


def run_plant(t: OrgDb, sql: str) -> list[tuple[str, str]]:
    with t.admin(autocommit=False) as c:
        try:
            c.execute(sql)
            return [tuple(r) for r in c.execute("SELECT rule, object FROM kernel.rls_violations")]
        finally:
            c.rollback()


def test_the_compliant_function_is_clean(vdb):
    assert run_plant(vdb, plant()) == []
    assert run_plant(vdb, plant(vol="VOLATILE", returns="boolean", reg=("write", "pipeshub_node", "org", False), grants=("pipeshub_node",), body=WRITE_BODY)) == []


@pytest.mark.parametrize("case", CHECKLIST, ids=[c[0] for c in CHECKLIST])
def test_checklist_violation_is_caught(vdb, case):
    _, sql, rule = case
    found = run_plant(vdb, sql)
    assert any(r.startswith(rule) for r, _ in found), f"{rule!r} not reported; got {sorted({r for r, _ in found})}"


def test_a_content_column_is_allowed_when_the_function_is_registered_for_it(vdb):
    sql = plant(returns="TABLE (id text, body text)", body=swap("SELECT n.id FROM", "SELECT n.id, n.body FROM"), reg=("read", None, "org", True))
    assert run_plant(vdb, sql) == []


def test_every_checklist_item_has_a_planted_violation():
    items = {c[0].split()[0] for c in CHECKLIST if c[0][0].isdigit()}
    assert items >= {"1", "2", "3", "4", "5", "6", "7", "8", "11"}
    # 9 (tests exist) and 10 (the index is used from the seventh call on) are enforced by the manifest check and the harness
    assert {"9", "10"}.isdisjoint(items)


def test_the_catalog_check_numbers_55_to_73_each_have_a_plant(vdb):
    covered = {rule[:2] for _, _, rule in CHECKLIST}
    assert covered >= {"55", "56", "57", "58", "59", "60", "61", "62", "63", "64", "65", "66", "67", "68", "70", "72", "73", "74", "75"}, sorted(covered)


def test_function_registry_rejects_a_helper_that_is_not_a_read_function(vdb):
    with vdb.admin() as c, pytest.raises(pg.CheckViolation):
        c.execute("SELECT kernel.register_function('kernel.org_lineage(text)', 'jobs', NULL, 'helper')")


# ---- the manifest: item 9 and the registry --------------------------------------------------------------------------

def test_a_registered_function_missing_from_the_manifests_is_reported(vdb):
    with vdb.admin(autocommit=False) as c:
        try:
            c.execute(plant())
            found = vetted.problems(c, FNS)
        finally:
            c.rollback()
    assert ("71 vetted function is not in any manifest", "kt.plant(text)") in found


def test_a_manifest_that_disagrees_with_the_registry_is_reported(vdb):
    with vdb.admin(autocommit=False) as c:
        try:
            c.execute("UPDATE kernel.function_registry SET class = 'write', writer = 'pipeshub_node' WHERE proc = 'kt.notes_by_title(text,integer)'::regprocedure")
            found = vetted.problems(c, FNS)
        finally:
            c.rollback()
    assert ("71 manifest and function registry disagree", "kt.notes_by_title(text,integer)") in found


def test_a_manifest_entry_that_reads_a_table_without_vetted_read_is_reported(vdb):
    fn = FNS["kt.notes_by_title(text, int)"]
    with vdb.admin() as c:
        found = vetted.problems(c, {**FNS, fn.signature: dataclasses.replace(fn, reads=("kt.secrets",))})
    assert ("71 function reads a table that did not opt into vetted_read", "kt.notes_by_title(text,integer) -> kt.secrets") in found


def test_a_manifest_function_that_is_not_deployed_is_reported(vdb):
    ghost = dataclasses.replace(FNS["kt.notes_by_title(text, int)"], name="ghost")
    with vdb.admin() as c:
        assert ("71 manifest function is not deployed and registered", ghost.signature) in vetted.problems(c, {**FNS, ghost.signature: ghost})


def test_an_invalid_manifest_is_a_violation_not_a_crash(vdb, tmp_path, monkeypatch):
    bad = tmp_path / "manifest.yaml"
    bad.write_text("functions:\n  - {name: kt.x, class: nope, returns: int, body: 'RETURN 1;'}\n")
    with pytest.raises(vetted.ManifestError):
        vetted.load(bad)
    monkeypatch.setattr(vetted, "manifest_files", lambda: [bad])
    with vdb.admin() as c:
        found = vetted.problems(c)
    assert [r for r, _ in found] == ["71 vetted function manifest is invalid"]


# ---- the generator -----------------------------------------------------------------------------------------------------

def entry(**kw) -> dict:
    base = {"name": "kt.f", "class": "read", "scope": "org", "args": [{"name": "p_x", "type": "text", "length": [1, 8]}],
            "returns": "TABLE (id text)", "body": "RETURN QUERY SELECT 'a' LIMIT 1;"}
    return {**base, **kw}


@pytest.mark.parametrize("kw, message", [
    ({"class": "nope"}, "class must be one of"),
    ({"scope": "everywhere"}, "scope must be one of"),
    ({"class": "write"}, "exactly the write class names a writer"),
    ({"writer": "pipeshub_node"}, "exactly the write class names a writer"),
    ({"org_arg": "p_x"}, "never from an argument"),
    ({"scope": "lineage"}, "org_arg"),
    ({"scope": "helper", "grants": ["pipeshub_app"]}, "no grants"),
    ({"returns": "int", "scope": "org"}, "needs no_org"),
    ({"reads": ["ku.items"]}, "outside the function's schema"),
    ({"body": ""}, "body is required"),
    ({"body": "RETURN $fn$;"}, "body is required"),
    ({"name": "Kt.F"}, "lower case"),
    ({"surprise": 1}, "unknown keys"),
    ({"args": [{"name": "p_x", "type": "text; DROP TABLE x"}]}, "bad argument"),
    ({"args": [{"name": "p_x", "type": "text", "length": [1]}]}, "expected [min, max]"),
    # free text in a field that is pasted into SQL
    ({"args": [{"name": "p_x", "type": "text", "default": "'a'; SELECT 'INJECTED'"}]}, "a default must be"),
    ({"args": [{"name": "p_x", "type": "text", "default": "1) SELECT 1 --"}]}, "a default must be"),
    ({"grants": ["pipeshub_app; SELECT 'INJECTED'"]}, "a grant must be an identifier"),
    ({"grants": ["pipeshub_app, public"]}, "a grant must be an identifier"),
    ({"grants": "pipeshub_app"}, "grants must be a list"),
    ({"class": "write", "writer": "pipeshub_node; SELECT 'INJECTED'"}, "writer must be an identifier"),
    ({"class": "write", "writer": "PUBLIC"}, "writer must be an identifier"),
    ({"returns": "int", "no_org": "1; SELECT 'INJECTED'"}, "no_org must be"),
    ({"returns": "int", "no_org": "$fn$ SELECT 1 $fn$"}, "no_org must be"),
    ({"returns": "int", "no_org": "(SELECT 1)"}, "no_org must be"),
    ({"returns": "TABLE (id text) /* x */"}, "returns must be"),
    ({"returns": "int; SELECT 1"}, "returns must be"),
    ({"returns": "TABLE (id text, \"b c\" int)"}, "returns must be"),
    ({"args": [{"name": "p_x", "type": "text) ; SELECT 1 --"}]}, "bad argument"),
    ({"args": [{"name": "p x", "type": "text"}]}, "an argument name must be an identifier"),
    ({"args": [{"name": "user", "type": "text"}]}, "an argument name must be an identifier"),
    ({"args": [{"name": "p_x", "type": "text", "nullable": "yes"}]}, "nullable must be"),
    ({"args": [{"name": "p_x", "type": "text", "max_items": "3; SELECT 1"}]}, "max_items must be"),
    ({"scope": "lineage", "org_arg": "p_x; SELECT 1"}, "org_arg must be an identifier"),
    ({"ext": "yes"}, "ext must be true or false"),
    ({"reads": ["kt.x; SELECT 1"]}, "a table name must be an identifier"),
    ({"body": "RAISE NOTICE 'x';"}, "RAISE is allowed only"),
    ({"body": "RAISE EXCEPTION 'x %', p_x USING ERRCODE = '22023';"}, "RAISE is allowed only"),
    ({"body": "RAISE EXCEPTION 'x' USING ERRCODE = '22023', DETAIL = 'd';"}, "RAISE is allowed only"),
])
def test_manifest_entries_that_break_the_template_are_refused(kw, message):
    with pytest.raises(vetted.ManifestError, match=re.escape(message)):
        vetted.parse(entry(**kw))


def test_what_the_generator_emits_is_always_a_constant_exception():
    for fn in ALL:
        if fn.schema in ("kt", "ku", "kernel"):
            assert vetted.bad_raises(vetted.render(fn).split("$fn$")[1]) == [], fn.qualified
    assert vetted.bad_raises("RAISE EXCEPTION 'a: b' USING ERRCODE = '22023'; RAISE NOTICE 'x';") == ["RAISE NOTICE 'x'"]


def test_identifiers_are_quoted_only_when_needed():
    assert vetted.qi("p_x") == "p_x" and vetted.qi("select") == '"select"' and vetted.qi('a"b') == '"a""b"' and vetted.qi("Mixed") == '"Mixed"'


def test_the_rendered_function_follows_the_template():
    fn = vetted.parse(entry(args=[{"name": "p_x", "type": "text", "length": [1, 8], "pattern": "^a+$"},
                                  {"name": "p_n", "type": "int", "default": "5", "range": [1, 9]},
                                  {"name": "p_k", "type": "text", "enum": ["a", "b"], "nullable": True},
                                  {"name": "p_l", "type": "text[]", "max_items": 3}], ext=True, custom_plan=True))
    sql = vetted.render(fn)
    assert "SET search_path = pg_catalog, kt, ext, pg_temp" in sql                 # pg_temp last
    assert "SET plan_cache_mode = force_custom_plan" in sql
    assert sql.count("ERRCODE = '22023'") == 8                                  # required x3, length, pattern, range, enum, items
    assert "p_k IS NOT NULL AND p_k NOT IN ('a', 'b')" in sql                       # a nullable argument is validated when present
    assert "IF v_org IS NULL THEN RETURN; END IF;" in sql
    assert "LANGUAGE plpgsql STABLE SECURITY DEFINER" in sql
    assert sql.index("SET LOCAL ROLE pipeshub_vetted_kt") < sql.index("CREATE FUNCTION") < sql.index("SET LOCAL ROLE pipeshub_owner")
    assert "REVOKE ALL ON FUNCTION kt.f(text, int, text, text[]) FROM PUBLIC;" in sql
    assert "GRANT EXECUTE ON FUNCTION kt.f(text, int, text, text[]) TO pipeshub_app;" in sql
    assert "REVOKE CREATE ON SCHEMA kt FROM pipeshub_vetted_kt;" in sql and "REVOKE pipeshub_vetted_kt FROM pipeshub_owner;" in sql
    assert "kernel.register_function('kt.f(text, int, text, text[])', 'read', NULL, 'org', false)" in sql


def test_a_hostile_argument_name_or_message_cannot_break_out_of_the_generated_sql():
    fn = vetted.parse(entry(args=[{"name": "p_x", "type": "text", "enum": ["a'); DROP TABLE kt.notes; --"]}]))
    assert "'a''); DROP TABLE kt.notes; --'" in vetted.render(fn)


def test_the_checked_in_sql_matches_the_shipped_manifest():
    expected = vetted.render_all(vetted.load(CORE))
    here = CORE.parent
    assert sorted(p.name for p in here.glob("*.sql") if p.name not in ("assert_profile_epoch.sql", "profile_epoch.sql")) == sorted(expected)      # the fence is hand-written
    for name, sql in expected.items():
        assert (here / name).read_text(encoding="utf-8") == sql, f"{name}: run python -m db.lib.vetted render {CORE}"
    assert vetted.main(["check", str(CORE)]) == 0


def test_only_a_named_extension_point_may_be_extended(tmp_path):
    ext = tmp_path / "m.yaml"
    lineage = vetted.load(CORE)[1]
    assert lineage.qualified == "kernel.org_lineage" and vetted.EXTENSION_POINTS == {"kernel.org_parent_ids"}
    ext.write_text(yaml.safe_dump({"functions": [{"name": "kernel.org_lineage", "class": "read", "scope": "lineage", "extends": True, "org_arg": "p_org",
        "returns": "text[]", "no_org": "'{}'", "args": [{"name": "p_org", "type": "text", "pattern": "^[0-9a-f]{24}$"}], "body": "RETURN ARRAY['x'];"}]}))
    with pytest.raises(vetted.ManifestError, match="not an extension point"):
        vetted.load_all([CORE, ext])


def test_an_extending_entry_must_keep_the_frozen_shape(tmp_path):
    core = vetted.load(CORE)[0]
    assert core.qualified == "kernel.org_parent_ids"
    ext = tmp_path / "m.yaml"

    def write(**kw):
        e = {"name": "kernel.org_parent_ids", "class": "read", "scope": "helper", "extends": True, "returns": "text[]", "body": "RETURN '{}';",
             "args": [{"name": "p_org", "type": "text"}], **kw}
        ext.write_text(yaml.safe_dump({"functions": [e]}))

    write()
    assert vetted.load_all([CORE, ext])["kernel.org_parent_ids(text)"].source == str(ext)
    for kw, why in (({"returns": "text"}, "frozen signature"), ({"args": [{"name": "p_org", "type": "text"}, {"name": "p_y", "type": "int"}]}, "frozen signature"),
                    ({"args": [{"name": "p_who", "type": "text"}]}, "frozen signature"), ({"extends": False}, "already defined"),
                    ({"scope": "global"}, "frozen signature"), ({"name": "kernel.other"}, "extends a function no manifest defines")):
        write(**kw)
        with pytest.raises(vetted.ManifestError, match=why):
            vetted.load_all([CORE, ext])


def test_the_lint_reads_generated_function_files():
    assert lint.main([]) == 0
    sql = vetted.render(vetted.parse(entry()))
    assert lint.lint_file(_tmp("vetted", "ok.sql", sql)) == []
    assert any("SECURITY DEFINER outside a vetted" in m for m in lint.lint_file(_tmp("plain", "bad.sql", sql)))
    assert any("GRANT CREATE on a schema" in m for m in lint.lint_file(_tmp("vetted", "bad2.sql", "GRANT CREATE ON SCHEMA kt TO pipeshub_node;")))


def _tmp(folder: str, name: str, text: str) -> Path:
    p = Path(os.environ.get("TMPDIR", "/tmp")) / f"lint-{os.getpid()}" / folder / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


def test_explain_sql_inlines_the_org_and_the_sample_arguments():
    fn = FNS["kt.notes_by_title(text, int)"]
    sql = vetted_harness.explain_sql(fn, ["it's", 5])
    assert "n.org_id = NULLIF(current_setting('app.org_id', true), '')" in sql and "n.title = 'it''s'" in sql and "LIMIT 5" in sql


# ---- per-schema owners ------------------------------------------------------------------------------------------------

def test_each_function_belongs_to_its_schemas_vetted_owner(vdb):
    with vdb.admin() as c:
        owners = dict(c.execute("SELECT p.oid::regprocedure::text, p.proowner::regrole::text FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                                "WHERE n.nspname IN ('kt', 'ku', 'kernel') AND p.prosecdef"))
    assert owners == {"kt.notes_by_title(text,integer)": "pipeshub_vetted_kt", "kt.set_note_title(text,text)": "pipeshub_vetted_kt",
                      "kt.token_owner(text)": "pipeshub_vetted_kt", "ku.item_labels(integer)": "pipeshub_vetted_ku",
                      "kernel.org_lineage(text)": "pipeshub_vetted_kernel", "kernel.org_parent_ids(text)": "pipeshub_vetted_kernel",
                      "kernel.assert_profile_epoch(integer)": "pipeshub_vetted_kernel", "kernel.profile_epoch()": "pipeshub_vetted_kernel",
                          "kernel.assert_store_epoch(integer)": "pipeshub_vetted_kernel", "kernel.postgres_store_epoch()": "pipeshub_vetted_kernel"}


def test_a_vetted_owner_holds_nothing_outside_its_schema_and_nobody_is_its_member(vdb):
    with vdb.admin() as c:
        for role, mine, other in (("pipeshub_vetted_kt", "kt.notes", "ku.items"), ("pipeshub_vetted_ku", "ku.items", "kt.notes")):
            assert c.execute("SELECT has_table_privilege(%s, %s, 'SELECT')", (role, mine)).fetchone()[0] is True
            assert c.execute("SELECT has_table_privilege(%s, %s, 'SELECT')", (role, other)).fetchone()[0] is False
        assert c.execute("SELECT has_table_privilege('pipeshub_vetted_kt', 'kt.secrets', 'SELECT')").fetchone()[0] is False
        members = c.execute("SELECT m.member::regrole::text, m.inherit_option, m.set_option FROM pg_auth_members m "
                            "WHERE m.roleid IN (SELECT oid FROM pg_roles WHERE rolname LIKE 'pipeshub\\_vetted\\_%')").fetchall()
        assert set(members) == {("pipeshub_owner", False, False)}                  # ADMIN only: it can hand the role out, never act as it


def test_a_function_of_one_schema_cannot_read_another_schemas_tables(vdb):
    """A bad ku function that names kt.notes: the vetted_read policy on kt.notes exists, but the ku owner holds no grant on it."""
    sql = plant(name="ku.peek", owner="pipeshub_vetted_ku", path="pg_catalog, ku, pg_temp", body=swap("kt.notes", "kt.notes"))
    with vdb.admin(autocommit=False) as c:
        try:
            c.execute(sql)
            c.execute("GRANT USAGE ON SCHEMA ku TO pipeshub_app")
            c.execute("SET LOCAL ROLE pipeshub_node")
            c.execute("SELECT set_config('app.org_id', %s, true)", (ORG_B,))
            with pytest.raises(pg.InsufficientPrivilege):
                c.execute("SELECT * FROM ku.peek('x')").fetchall()
        finally:
            c.rollback()


def test_a_function_cannot_read_a_table_that_did_not_opt_into_vetted_reads(vdb):
    sql = plant(body=swap("kt.notes n", "kt.secrets n").replace("n.title = p_x", "n.secret = p_x"))
    with vdb.admin(autocommit=False) as c:
        try:
            c.execute(sql)
            c.execute("SET LOCAL ROLE pipeshub_node")
            c.execute("SELECT set_config('app.org_id', %s, true)", (ORG_B,))
            with pytest.raises(pg.InsufficientPrivilege):
                c.execute("SELECT * FROM kt.plant('x')").fetchall()
        finally:
            c.rollback()


# ---- a compromised caller cannot cross orgs through a vetted function -----------------------------------------------------

def caller(t: OrgDb, org: str | None = ORG_A):
    """A service connection inside a unit of work for `org`, holding TEMP like a writer that COPYs."""
    c = t.pooled("node")
    return c, uow(c, "pipeshub_node", org)


def test_the_callers_search_path_cannot_redirect_the_function(vdb):
    fn = FNS["kt.notes_by_title(text, int)"]
    seed(vdb, fn)
    own = invoke(vdb, fn, ORG_B, ["findme", 5])
    assert own == [["needle-1", "findme"]]
    with vdb.pooled("node") as c, uow(c, "pipeshub_node", ORG_B):
        c.execute("CREATE TEMP TABLE notes (org_id text, id text, title text, body text) ON COMMIT DROP")
        c.execute("INSERT INTO pg_temp.notes VALUES (%s, 'forged', 'findme', 'x')", (ORG_B,))
        c.execute("CREATE FUNCTION pg_temp.length(text) RETURNS int LANGUAGE sql AS 'SELECT 1'")        # would make an empty title valid
        c.execute("CREATE FUNCTION pg_temp.lower(text) RETURNS text LANGUAGE sql AS $$SELECT 'findme'$$")
        for path in ("pg_temp, kt, public", "pg_temp", "kt, pg_temp, pg_catalog", "public, pg_temp"):
            c.execute("SELECT set_config('search_path', %s, true)", (path,))
            assert rows(c.execute("SELECT * FROM kt.notes_by_title('findme', 5)")) == own, path
            with pytest.raises(pg.InvalidParameterValue), c.transaction():
                c.execute("SELECT * FROM kt.notes_by_title('', 5)")


ATTACKS = ["findme' OR org_id <> '", "%", "\\", "findme'; DELETE FROM kt.notes; --", "findme\x00", "' UNION SELECT secret FROM kt.secrets --", "findme" * 20]


@pytest.mark.parametrize("attack", ATTACKS, ids=range(len(ATTACKS)))
def test_argument_injection_returns_nothing_of_another_org(vdb, attack):
    fn = FNS["kt.notes_by_title(text, int)"]
    seed(vdb, fn)
    try:
        got = invoke(vdb, fn, ORG_A, [attack, 5])
    except (pg.InvalidParameterValue, psycopg.DataError):
        got = []                                                             # refused by validation (a NUL byte, 140 characters)
    assert got == []
    with vdb.admin() as c:
        assert c.execute("SELECT count(*) FROM kt.notes WHERE org_id = %s AND id = 'needle-1'", (ORG_B,)).fetchone()[0] == 1


def test_a_text_array_or_pattern_argument_cannot_widen_the_org(vdb):
    lineage = FNS["kernel.org_lineage(text)"]
    for attack in (f"{ORG_A},{ORG_B}", f"{ORG_B}", f"{ORG_A}' OR '1'='1", f"{ORG_A}\n{ORG_B}", ORG_A.upper(), ""):
        try:
            got = invoke(vdb, lineage, ORG_A, [attack])
        except pg.InvalidParameterValue:
            continue
        assert got == [[[]]], attack                                         # someone else's org answers like no org


def test_security_definer_misuse_by_a_caller_is_refused(vdb):
    attempts = [
        "SET ROLE pipeshub_vetted_kt",
        "SET ROLE pipeshub_vetted_kernel",
        "SET ROLE pipeshub_owner",
        "CREATE FUNCTION kt.evil() RETURNS int LANGUAGE sql SECURITY DEFINER AS 'SELECT 1'",
        "CREATE OR REPLACE FUNCTION kt.notes_by_title(text, int) RETURNS TABLE (id text, title text) LANGUAGE sql AS 'SELECT id, org_id FROM kt.notes'",
        "CREATE OR REPLACE FUNCTION kernel.org_lineage(text) RETURNS text[] LANGUAGE sql AS $$SELECT ARRAY['x']$$",
        "ALTER FUNCTION kt.notes_by_title(text, int) SET search_path = pg_temp",
        "ALTER FUNCTION kt.notes_by_title(text, int) OWNER TO pipeshub_node",
        "ALTER FUNCTION kt.notes_by_title(text, int) SECURITY INVOKER",
        "DROP FUNCTION kt.notes_by_title(text, int)",
        "GRANT EXECUTE ON FUNCTION kernel.org_parent_ids(text) TO pipeshub_node",
        f"SELECT kernel.org_parent_ids('{ORG_A}')",
        "SELECT kernel.register_function('kt.notes_by_title(text,integer)', 'read', NULL, 'global', true)",
        "UPDATE kernel.function_registry SET scope = 'global'",
        "SELECT * FROM kt.tokens",
    ]
    with vdb.pooled("node") as c:
        for sql in attempts:
            try:
                with uow(c, "pipeshub_node", ORG_A):
                    c.execute(sql)
            except pg.InsufficientPrivilege:
                continue
            raise AssertionError(f"was allowed: {sql}")


def test_other_service_roles_and_jobs_cannot_call_a_write_function_or_a_helper(vdb):
    write = FNS["kt.set_note_title(text, text)"]
    for role in ("pipeshub_connectors", "pipeshub_indexing", "pipeshub_query", "pipeshub_jobs"):
        with vdb.admin() as c, c.transaction(force_rollback=True), pytest.raises(pg.InsufficientPrivilege):
            c.execute(pgsql.SQL("SET LOCAL ROLE {}").format(pgsql.Identifier(role)))
            c.execute("SELECT kt.set_note_title('needle-1', 'x')")
    assert write.writer == "pipeshub_node"


def test_a_foreign_id_and_an_unknown_id_look_the_same_to_a_vetted_write(vdb):
    fn = FNS["kt.set_note_title(text, text)"]
    seed(vdb, fn)
    foreign = invoke(vdb, fn, ORG_A, ["needle-1", "hacked"])
    unknown = invoke(vdb, fn, ORG_A, ["no-such-id", "hacked"])
    assert foreign == unknown == [[False]]
    assert state(vdb, fn) == [["findme"]]


# ---- kernel.org_lineage ----------------------------------------------------------------------------------------------------

def test_lineage_is_the_org_alone_in_the_shipped_edition(vdb):
    fn = FNS["kernel.org_lineage(text)"]
    assert invoke(vdb, fn, ORG_A, [ORG_A]) == [[[ORG_A]]]
    assert invoke(vdb, fn, ORG_B, [ORG_B]) == [[[ORG_B]]]
    assert invoke(vdb, fn, ORG_A, [ORG_B]) == [[[]]]
    assert invoke(vdb, fn, None, [ORG_A]) == [[[]]]


def test_the_parent_lookup_returns_nothing_in_the_shipped_edition(vdb):
    with vdb.admin() as c:
        assert c.execute("SELECT kernel.org_parent_ids(%s)", (ORG_A,)).fetchone()[0] == []                          # as the superuser it can be called


def test_lines_of_another_postgresql_major_are_ignored():
    lines = ["sysfuncs@17 count=1 md5=a", "sysfuncs@18 count=2 md5=b", "role x"]
    assert surface.for_major(lines, 17) == {"sysfuncs@17 count=1 md5=a", "role x"}
    assert surface.for_major(lines, 18) == {"sysfuncs@18 count=2 md5=b", "role x"}


def test_an_allowlist_can_remove_a_line_of_the_base_state(tmp_path):
    f = tmp_path / "allowlist.yaml"
    f.write_text('surface:\n  - "new line"\nremove:\n  - "old line"\n')
    base = tmp_path / "surface.txt"
    base.write_text("old line\nkept line\n")
    assert surface.expected_lines([base, f]) == {"new line", "kept line"}
