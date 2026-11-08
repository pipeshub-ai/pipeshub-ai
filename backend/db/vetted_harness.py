"""The plan gate for vetted functions: what plans do the statements inside a function get, with the org's data in the tables?

``plans_auto_explain``: load auto_explain in an administrator's session (LOAD needs superuser), switch to the service role and
call the function several times; the plans of the statements inside it come back as notices. This is the real thing: the
plpgsql plan cache switches from custom to generic plans after five calls, so calls one and seven are both checked.

``plan_explain``: where LOAD is not allowed (managed platforms), switch to the schema's vetted role and EXPLAIN the function's
query with the org set. It shows the shape the planner picks for the vetted role's ``vetted_read`` policy but not the plan
cache behaviour, so a function that needs it also sets ``custom_plan`` in its manifest entry.

``check_plans`` is the gate. It fails when (a) any plan scans an org-scoped table sequentially and the table holds more than
``min_rows`` rows (statistics from ANALYZE), or (b) the index the manifest names (matched as a whole name, ``x_idx`` is not
``x_idx2``) is absent from the plans of calls one and seven. It picks auto_explain and falls back to EXPLAIN by itself.
"""
import re

import psycopg
from psycopg import sql as pgsql

from db.lib.vetted import Fn

SEQ_SCAN = re.compile(r"Seq Scan on (?:\"?\w+\"?\.)?\"?(\w+)\"?")
MIN_ROWS = 1000


def plan_uses_index(plan: str, index: str) -> bool:
    """The index appears as a scan node's whole name: 'Index Scan using idx', 'Index Only Scan using idx', 'Bitmap Index Scan on idx'."""
    return re.search(rf"Index (?:Only )?Scan(?: Backward)? using {re.escape(index)}(?![\w$])|Bitmap Index Scan on {re.escape(index)}(?![\w$])", plan) is not None


def seq_scanned(plan: str, big: dict[str, int]) -> list[str]:
    """Names of the org-scoped tables in ``big`` (name -> rows) that the plan scans sequentially."""
    return sorted({t for t in SEQ_SCAN.findall(plan) if t in big})


def org_tables_over(conn: psycopg.Connection, min_rows: int) -> dict[str, int]:
    """Every relation of an org-scoped (org or lineage) tree with more than ``min_rows`` estimated rows; partition leaves by their own name."""
    rows = conn.execute(
        "SELECT c.relname, GREATEST(c.reltuples, 0)::bigint FROM pg_class c WHERE c.relkind = 'r' AND (c.oid IN "
        "(SELECT rel FROM kernel.rls_registry WHERE class IN ('org', 'lineage')) OR c.oid IN "
        "(SELECT t.relid FROM kernel.rls_registry r, LATERAL pg_partition_tree(r.rel) t WHERE r.class IN ('org', 'lineage')))").fetchall()
    return {n: int(k) for n, k in rows if k > min_rows}


def plans_auto_explain(admin: psycopg.Connection, role: str, org: str, sql: str, params: tuple = (), calls: int = 7, user: str | None = None) -> list[str]:
    """One text per call: the plans auto_explain logged for the statements the call ran. Needs superuser."""
    notes: list[str] = []

    def keep(d: psycopg.errors.Diagnostic) -> None:
        notes.append(d.message_primary or "")

    admin.add_notice_handler(keep)
    plans = []
    try:
        with admin.transaction():
            admin.execute("LOAD 'auto_explain'")
            admin.execute("SET LOCAL auto_explain.log_min_duration = 0")
            admin.execute("SET LOCAL auto_explain.log_nested_statements = on")
            admin.execute("SET LOCAL auto_explain.log_level = notice")
            admin.execute(pgsql.SQL("SET LOCAL ROLE {}").format(pgsql.Identifier(role)))
            admin.execute("SELECT set_config('app.org_id', %s, true), set_config('app.user_id', %s, true)", (org, user or ""))
            for _ in range(calls):
                notes.clear()
                admin.execute(sql, params).fetchall()
                plans.append("\n".join(notes))
            admin.execute("RESET ROLE")          # SET LOCAL outlives a savepoint: the caller may be inside a transaction
    finally:
        admin.remove_notice_handler(keep)
    return plans


def explain_sql(fn: Fn, args: list) -> str:
    """The function's first RETURN QUERY statement with the org read from the GUC and the sample arguments inlined."""
    m = re.search(r"RETURN\s+QUERY\s+(.*?);", fn.body, re.S | re.I)
    if not m:
        raise ValueError(f"{fn.qualified}: no RETURN QUERY statement to explain")
    sql = m.group(1).replace("v_org", "NULLIF(current_setting('app.org_id', true), '')")
    for a, v in zip(fn.args, args, strict=False):
        lit = "NULL" if v is None else str(v) if isinstance(v, int) and not isinstance(v, bool) else "'" + str(v).replace("'", "''") + "'"
        sql = re.sub(rf"\b{a.name}\b", lit, sql)
    return sql


def plan_explain(admin: psycopg.Connection, vetted_role: str, org: str, sql: str, user: str | None = None) -> str:
    """EXPLAIN as the schema's vetted role. The connection's user must be allowed to SET ROLE to it (CI: a superuser)."""
    with admin.transaction():
        admin.execute(pgsql.SQL("SET LOCAL ROLE {}").format(pgsql.Identifier(vetted_role)))
        admin.execute("SELECT set_config('app.org_id', %s, true), set_config('app.user_id', %s, true)", (org, user or ""))
        plan = "\n".join(r[0] for r in admin.execute("EXPLAIN (COSTS OFF) " + sql))
        admin.execute("RESET ROLE")
        return plan


def check_plans(admin: psycopg.Connection, fn: Fn, org: str, call_sql: str, args: list, role: str, index: str | None = None,
                mode: str = "auto", min_rows: int = MIN_ROWS, user: str | None = None) -> list[str]:
    """Problems found (empty = the gate passes). ``mode``: auto | auto_explain | explain."""
    big = org_tables_over(admin, min_rows)
    if mode in ("auto", "auto_explain"):
        try:
            plans = plans_auto_explain(admin, role, org, call_sql, tuple(args), user=user)
        except (psycopg.errors.InsufficientPrivilege, psycopg.errors.UndefinedFile, psycopg.errors.UndefinedObject):
            if mode == "auto_explain":
                raise
        else:
            out = [f"call {i + 1} scans {t} sequentially ({big[t]} rows)" for i, p in enumerate(plans) for t in seq_scanned(p, big)]
            if index:
                out += [f"call {i + 1} does not use index {index}" for i in (0, 6) if not plan_uses_index(plans[i], index)]
            return out
    plan = plan_explain(admin, fn.owner, org, explain_sql(fn, args), user)
    out = [f"EXPLAIN scans {t} sequentially ({big[t]} rows)" for t in seq_scanned(plan, big)]
    return out + ([f"EXPLAIN does not use index {index}"] if index and not plan_uses_index(plan, index) else [])
