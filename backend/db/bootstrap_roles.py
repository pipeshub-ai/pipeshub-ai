"""Create the role layout the migrations and the catalog check expect: ``python -m db.bootstrap_roles [--vetted a,b]``.

Idempotent and additive: a missing role is created, a missing membership is granted, an existing role is never
altered (passwords included). Needs an administrative credential (``PG_ADMIN_URL``: CREATEROLE, and database
ownership for the REVOKEs). No role created here has SUPERUSER, CREATEROLE or BYPASSRLS.

Logins get a password only when the matching variable is set (``PG_NODE_PASSWORD``, ``PG_MIGRATE_PASSWORD`` ...); ``pipeshub_ro`` and ``pipeshub_restore`` never get one here (a restore window sets its own).
``PG_BOOTSTRAP_EXTENSIONS`` (comma list, e.g. ``vector,pg_trgm``) installs extensions into schema ``ext``.
"""
import contextlib
import os
import re
import sys
from collections.abc import Iterable, Mapping

import psycopg
from psycopg import sql

SERVICES = ("node", "connectors", "indexing", "query", "kv")   # kv: key-value table only, for the processes that just read configuration
PASSWORD_VARS = {"pipeshub_migrate": "PG_MIGRATE_PASSWORD", "pipeshub_jobs": "PG_JOBS_PASSWORD",
                 "pipeshub_backup": "PG_BACKUP_PASSWORD", **{f"pipeshub_{s}_login": f"PG_{s.upper()}_PASSWORD" for s in SERVICES}}
# (role, can log in)
ROLES = [("pipeshub_owner", False), ("pipeshub_migrate", True), ("pipeshub_app", False),
         *[(f"pipeshub_{s}", False) for s in SERVICES], *[(f"pipeshub_{s}_login", True) for s in SERVICES],
         ("pipeshub_jobs", True), ("pipeshub_ro", True), ("pipeshub_backup", True), ("pipeshub_restore", False),
         ("pipeshub_import", False)]   # the import role logs in only while an operator is moving data (the move script enables it)
# (role, member, inherit, set): service roles inherit the policy group but cannot become it; a login can only
# SET into its own service role, so it holds no privileges until the unit of work switches role.
GRANTS = [("pipeshub_owner", "pipeshub_migrate", True, True), ("pipeshub_app", "pipeshub_ro", True, False),
          *[("pipeshub_app", f"pipeshub_{s}", True, False) for s in SERVICES],
          *[(f"pipeshub_{s}", f"pipeshub_{s}_login", False, True) for s in SERVICES]]
# Server-side large objects have no org: any role that may call these reads and writes them across orgs.
LARGE_OBJECTS_SQL = """DO $$ DECLARE f regprocedure; BEGIN
  FOR f IN SELECT p.oid::regprocedure FROM pg_proc p WHERE p.pronamespace = 'pg_catalog'::regnamespace
           AND p.proname ~ '^(lo_|lo(read|write)$)' LOOP
    EXECUTE format('REVOKE EXECUTE ON FUNCTION %s FROM PUBLIC', f);
  END LOOP; END $$"""
SHIPPED_VETTED = ("kernel", "platform")      # schemas of the shipped revisions that own SECURITY DEFINER functions
_SCHEMA = re.compile(r"^[a-z][a-z0-9_]*$")


def bootstrap(conn: psycopg.Connection, vetted_schemas: Iterable[str] = (), passwords: Mapping[str, str] | None = None,
              extensions: Iterable[str] = ()) -> list[str]:
    """Apply the layout on an autocommit admin connection; returns what it created.

    ``extensions`` are installed into schema ``ext`` (created here when missing, owned by the owner role): an extension such as
    pgvector needs a superuser, so it is the administrator's step, never a migration's."""
    passwords = passwords or {}
    vetted = [f"pipeshub_vetted_{s}" for s in dict.fromkeys([*SHIPPED_VETTED, *vetted_schemas])]   # the shipped schemas' functions need theirs
    if bad := [s for s in vetted_schemas if not _SCHEMA.match(s)]:
        raise ValueError(f"not a schema name: {bad}")
    existing = {r[0] for r in conn.execute("SELECT rolname FROM pg_roles")}
    made = []
    for name, login in [*ROLES, *[(v, False) for v in vetted]]:
        if name in existing:
            continue
        stmt = sql.SQL("CREATE ROLE {} {}").format(sql.Identifier(name), sql.SQL("LOGIN" if login else "NOLOGIN"))
        if login and passwords.get(name):
            stmt += sql.SQL(" PASSWORD {}").format(sql.Literal(passwords[name]))
        conn.execute(stmt)
        made.append(name)
    # The owner may only hand out membership of a vetted role (ADMIN), never use it: a revision that creates
    # that schema's SECURITY DEFINER functions grants itself SET for its own transaction and revokes it.
    grants = [*GRANTS, *[(v, "pipeshub_owner", False, False) for v in vetted]]
    for role, member, inherit, set_ in grants:
        conn.execute(sql.SQL("GRANT {} TO {} WITH {} INHERIT {}, SET {}").format(
            sql.Identifier(role), sql.Identifier(member), sql.SQL("ADMIN TRUE," if role in vetted else ""),
            sql.SQL(str(inherit).upper()), sql.SQL(str(set_).upper())))
    db = sql.Identifier(conn.info.dbname)
    conn.execute(sql.SQL("GRANT CREATE ON DATABASE {} TO pipeshub_owner").format(db))
    conn.execute(sql.SQL("REVOKE TEMPORARY ON DATABASE {} FROM PUBLIC").format(db))   # a warning unless we own the database
    conn.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
    with contextlib.suppress(psycopg.errors.InsufficientPrivilege):
        conn.execute(LARGE_OBJECTS_SQL)       # needs the owner of the system functions; the catalog check reports it otherwise
    if extensions := list(extensions):
        if bad := [e for e in extensions if not _SCHEMA.match(e)]:
            raise ValueError(f"not an extension name: {bad}")
        conn.execute("CREATE SCHEMA IF NOT EXISTS ext AUTHORIZATION pipeshub_owner")
        for ext in extensions:
            conn.execute(sql.SQL("CREATE EXTENSION IF NOT EXISTS {} WITH SCHEMA ext").format(sql.Identifier(ext)))
    return made


def main(argv: list[str]) -> int:
    url = os.environ.get("PG_ADMIN_URL", "").replace("postgresql+psycopg://", "postgresql://")
    if not url:
        print("bootstrap_roles: PG_ADMIN_URL is not set", file=sys.stderr)
        return 2
    schemas = argv[1].split(",") if len(argv) == 2 and argv[0] == "--vetted" else []
    if argv and not schemas:
        print("usage: python -m db.bootstrap_roles [--vetted schema,schema]   (PG_BOOTSTRAP_EXTENSIONS=vector,pg_trgm installs extensions)", file=sys.stderr)
        return 2
    extensions = [e for e in os.environ.get("PG_BOOTSTRAP_EXTENSIONS", "").split(",") if e]
    passwords = {role: os.environ[var] for role, var in PASSWORD_VARS.items() if os.environ.get(var)}
    with psycopg.connect(url, autocommit=True) as conn:
        made = bootstrap(conn, schemas, passwords, extensions)
    print("bootstrap_roles: created " + (", ".join(made) or "nothing (layout already in place)"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
