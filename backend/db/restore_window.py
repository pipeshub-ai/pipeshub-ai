"""Restore windows: ``python -m db.restore_window open --org <id> [--hours N] | sequences | close [--abandon]``.

A logical restore inserts as ``pipeshub_restore``, a NOLOGIN role that holds INSERT only, and only for rows of the one org
whose window is open (``kernel.restore_window``; the INSERT policy calls ``kernel.restore_allows``). The window is
maintenance mode: opening it switches the application logins off and disconnects them, refuses unless that org
has no rows yet, and gives the restore role a login that expires by itself (VALID UNTIL). Global tables are not
restorable by this role; they load through the vetted import functions. Sequences are set afterwards by the
migrator from the restored data (the restore role cannot call setval), and ``close`` refuses until that happened.

Credentials (environment): ``PG_ADMIN_URL`` (CREATEROLE: switches logins), ``PG_MIGRATE_URL`` (owns the window row),
``PG_BACKUP_URL`` (reads every org, to prove emptiness and to find sequence maxima), ``PG_RESTORE_PASSWORD``
(the restore login's password for this window). ``export --org <id> --dir <d>`` (migrate and backup credentials) writes one CSV per table with
ONLY that org's rows; ``load --org <id> --dir <d>`` (``PG_RESTORE_URL``) inserts them in key order and refuses, loudly and before
inserting, a file holding another org's rows: a multi-row INSERT with one foreign row is rejected whole by the database.
"""
import csv
import os
import re
import sys
import time
from pathlib import Path

import psycopg
from psycopg import sql

EXIT_REFUSED = 4


class Refused(Exception):
    """The window rules refuse this operation."""


def _connect(var: str) -> psycopg.Connection:
    url = os.environ.get(var, "").replace("postgresql+psycopg://", "postgresql://")
    if not url:
        raise Refused(f"{var} is not set")
    return psycopg.connect(url, autocommit=True)


def _settings(migrate: psycopg.Connection) -> dict:
    cols = ["backup", "restore", "migrate", "owner", "app_group", "role_prefix", "vetted_prefix", "services", "org_id_pattern", "importer"]
    row = migrate.execute(sql.SQL("SELECT {} FROM kernel.settings").format(sql.SQL(", ").join(map(sql.Identifier, cols)))).fetchone()
    return dict(zip(cols, row, strict=True))


def _app_logins(admin: psycopg.Connection, s: dict, *, include_disabled: bool = False) -> list[str]:
    """Roles of ours that are logins by design: everything with our prefix except the roles that never log in."""
    never = [s["owner"], s["app_group"], s["migrate"], s["backup"], s["restore"], s["importer"], *s["services"]]
    return [r[0] for r in admin.execute(
        "SELECT rolname FROM pg_roles WHERE (rolcanlogin OR %s) AND rolname LIKE %s AND NOT rolname = ANY(%s) AND rolname NOT LIKE %s ORDER BY 1",
        (include_disabled, s["role_prefix"] + "%", never, s["vetted_prefix"] + "%"))]


def _set_login(admin: psycopg.Connection, roles: list[str], *, login: bool) -> None:
    for r in roles:
        admin.execute(sql.SQL("ALTER ROLE {} {}").format(sql.Identifier(r), sql.SQL("LOGIN" if login else "NOLOGIN")))


def _disconnect(admin: psycopg.Connection, roles: list[str]) -> int:
    return admin.execute("SELECT count(*) FROM (SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                         "WHERE usename = ANY(%s) AND pid <> pg_backend_pid()) t", (roles,)).fetchone()[0]


def open_window(admin: psycopg.Connection, migrate: psycopg.Connection, backup: psycopg.Connection,
                org: str, password: str, hours: float = 4) -> None:
    s = _settings(migrate)
    if not re.match(s["org_id_pattern"], org):
        raise Refused(f"{org!r} is not an org id")
    if not 0 < hours <= 24:
        raise Refused("hours must be in (0, 24]")
    if migrate.execute("SELECT count(*) FROM kernel.restore_window").fetchone()[0]:
        raise Refused("a restore window is already open; close it first")
    logins = _app_logins(admin, s)
    # The row goes in BEFORE any login is switched off, so an interrupted run can always be closed.
    migrate.execute("INSERT INTO kernel.restore_window (org, until, disabled_logins) VALUES (%s, now() + make_interval(secs => %s), %s)",
                    (org, hours * 3600, logins))
    try:
        _set_login(admin, logins, login=False)              # maintenance mode: nothing application-side can connect
        for _ in range(50):                                 # a terminated backend takes a moment to exit
            _disconnect(admin, logins)
            left = admin.execute("SELECT count(*) FROM pg_stat_activity WHERE usename = ANY(%s)", (logins,)).fetchone()[0]
            if not left:
                break
            time.sleep(0.1)
        else:
            raise Refused(f"{left} application session(s) could not be disconnected")
        for (rel,) in migrate.execute("SELECT rel::text FROM kernel.rls_registry WHERE class NOT IN ('global', 'internal') ORDER BY 1").fetchall():
            if backup.execute(sql.SQL("SELECT 1 FROM {} WHERE org_id = %s LIMIT 1").format(sql.SQL(rel)), (org,)).fetchone():
                raise Refused(f"{rel} already has rows for {org}: a restore needs an empty org")
        # the server sees (and may log) a SCRAM verifier, never the password
        verifier = admin.pgconn.encrypt_password(password.encode(), s["restore"].encode(), b"scram-sha-256").decode()
        admin.execute(sql.SQL("ALTER ROLE {} LOGIN PASSWORD {} VALID UNTIL {}").format(
            sql.Identifier(s["restore"]), sql.Literal(verifier),
            sql.Literal(migrate.execute("SELECT until::text FROM kernel.restore_window").fetchone()[0])))
    except BaseException:
        migrate.execute("DELETE FROM kernel.restore_window")
        _set_login(admin, logins, login=True)
        raise


def restore_sequences(migrate: psycopg.Connection, backup: psycopg.Connection) -> list[str]:
    """setval every column-owned sequence to the restored maximum (never backwards). Returns the sequences set."""
    owned = migrate.execute(
        "SELECT s.oid::regclass::text, d.refobjid::regclass::text, a.attname FROM pg_class s "
        "JOIN pg_depend d ON d.objid = s.oid AND d.classid = 'pg_class'::regclass AND d.refclassid = 'pg_class'::regclass "
        "AND d.deptype IN ('a', 'i') JOIN pg_attribute a ON a.attrelid = d.refobjid AND a.attnum = d.refobjsubid "
        "JOIN pg_namespace n ON n.oid = s.relnamespace WHERE s.relkind = 'S' AND n.nspname NOT IN ('kernel', 'pg_catalog') "
        "AND NOT EXISTS (SELECT 1 FROM kernel.rls_registry g WHERE g.rel = d.refobjid AND g.class = 'internal') ORDER BY 1").fetchall()
    done = []
    for seq, rel, col in owned:
        top = backup.execute(sql.SQL("SELECT max({}) FROM {}").format(sql.Identifier(col), sql.SQL(rel))).fetchone()[0]
        if top is None:
            continue
        last, called = migrate.execute(sql.SQL("SELECT last_value, is_called FROM {}").format(sql.SQL(seq))).fetchone()
        if top > last or not called:
            migrate.execute("SELECT setval(%s::regclass, %s)", (seq, top))
            done.append(seq)
    migrate.execute("UPDATE kernel.restore_window SET sequences_restored = true")
    return done


def close_window(admin: psycopg.Connection, migrate: psycopg.Connection, *, abandon: bool = False) -> None:
    s = _settings(migrate)
    row = migrate.execute("SELECT disabled_logins, sequences_restored FROM kernel.restore_window").fetchone()
    if row and not row[1] and not abandon:
        raise Refused("sequences have not been restored (run `sequences`, or close with --abandon if nothing was loaded)")
    admin.execute(sql.SQL("ALTER ROLE {} NOLOGIN PASSWORD NULL VALID UNTIL 'infinity'").format(sql.Identifier(s["restore"])))
    _disconnect(admin, [s["restore"]])
    if row:
        migrate.execute("DELETE FROM kernel.restore_window")
        _set_login(admin, list(row[0]), login=True)
    elif abandon:                                           # no row: recover from an interrupted open
        _set_login(admin, _app_logins(admin, s, include_disabled=True), login=True)


def export_org(migrate: psycopg.Connection, backup: psycopg.Connection, org: str, directory: Path) -> list[str]:
    """Write one CSV per registered org-scoped table holding ONLY ``org``'s rows, parents before children, plus ``order.txt``."""
    tables = [r[0] for r in migrate.execute("SELECT rel::text FROM kernel.rls_registry WHERE class NOT IN ('global', 'internal')")]
    deps = {t: set() for t in tables}
    for child, parent in migrate.execute(
            "SELECT conrelid::regclass::text, confrelid::regclass::text FROM pg_constraint WHERE contype = 'f' AND conparentid = 0"):
        if child in deps and parent in deps and child != parent:
            deps[child].add(parent)
    ordered = []
    while deps:
        ready = sorted(t for t, d in deps.items() if d <= set(ordered))
        if not ready:
            raise Refused("foreign keys between registered tables form a cycle")
        ordered += ready
        for t in ready:
            del deps[t]
    directory.mkdir(parents=True, exist_ok=True)
    for t in ordered:
        with (directory / f"{t}.csv").open("wb") as f, backup.cursor().copy(
                sql.SQL("COPY (SELECT * FROM {} WHERE org_id = {}) TO STDOUT (FORMAT csv, HEADER, NULL '\\N')").format(sql.SQL(t), sql.Literal(org))) as cp:
            for chunk in cp:
                f.write(bytes(chunk))
    (directory / "order.txt").write_text("\n".join(ordered) + "\n", encoding="utf-8")
    return ordered


def restore_rows(restore: psycopg.Connection, org: str, table: str, columns: list[str], rows: list[list[str | None]]) -> int:
    """Insert ``rows`` for the open window's org, one transaction per call. Rows of any other org are never sent (one such
    row would make the database reject its whole batch): they are refused up front with a count per org, and nothing is inserted."""
    at = columns.index("org_id")
    foreign: dict[str, int] = {}
    for r in rows:
        if r[at] != org:
            foreign[r[at] or ""] = foreign.get(r[at] or "", 0) + 1
    if foreign:
        raise Refused(f"{table}: {sum(foreign.values())} row(s) belong to other orgs ({foreign}); nothing was inserted")
    if not rows:
        return 0
    cols = sql.SQL(", ").join(map(sql.Identifier, columns))
    stmt = sql.SQL("INSERT INTO {} ({}) OVERRIDING SYSTEM VALUE VALUES ({})").format(
        sql.SQL(table), cols, sql.SQL(", ").join(sql.Placeholder() * len(columns)))
    with restore.transaction(), restore.cursor() as cur:
        cur.executemany(stmt, rows)
    return len(rows)


def load_org(restore: psycopg.Connection, org: str, directory: Path, registered: set[str]) -> dict[str, int]:
    """Load what ``export_org`` wrote, in order, in ONE transaction: either every table is loaded or none is. Every file is
    checked first: a table name that is not in ``registered`` (the registry's own spelling) or a file holding another org's
    rows fails the whole load before anything is inserted. File contents never reach SQL as identifiers."""
    files = {}
    for table in (directory / "order.txt").read_text(encoding="utf-8").split():
        if table not in registered:
            raise Refused(f"{table!r} is not a registered table; nothing was inserted")
        with (directory / f"{table}.csv").open(newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            columns = next(reader)
            files[table] = (columns, [[None if v == "\\N" else v for v in r] for r in reader])
        at = columns.index("org_id")
        if foreign := sorted({r[at] or "" for r in files[table][1] if r[at] != org}):
            raise Refused(f"{table}: rows belong to other orgs ({foreign}); nothing was inserted")
    with restore.transaction():
        return {table: restore_rows(restore, org, table, columns, rows) for table, (columns, rows) in files.items()}


def main(argv: list[str]) -> int:
    cmd, args = (argv[0] if argv else ""), argv[1:]
    try:
        if cmd in ("export", "load") and len(args) == 4 and args[0] == "--org" and args[2] == "--dir":
            if cmd == "export":
                with _connect("PG_MIGRATE_URL") as migrate, _connect("PG_BACKUP_URL") as backup:
                    print(f"exported {len(export_org(migrate, backup, args[1], Path(args[3])))} table(s) of {args[1]}")
            else:
                with _connect("PG_MIGRATE_URL") as migrate:
                    registered = {r[0] for r in migrate.execute("SELECT rel::text FROM kernel.rls_registry WHERE class NOT IN ('global', 'internal')")}
                with _connect("PG_RESTORE_URL") as restore:
                    counts = load_org(restore, args[1], Path(args[3]), registered)
                print("loaded " + ", ".join(f"{t}={n}" for t, n in counts.items()))
            return 0
        with _connect("PG_ADMIN_URL") as admin, _connect("PG_MIGRATE_URL") as migrate:
            if cmd == "open" and args[:1] == ["--org"] and (len(args) == 2 or (len(args) == 4 and args[2] == "--hours")):
                if not os.environ.get("PG_RESTORE_PASSWORD"):
                    raise Refused("PG_RESTORE_PASSWORD is not set")
                with _connect("PG_BACKUP_URL") as backup:
                    open_window(admin, migrate, backup, args[1], os.environ["PG_RESTORE_PASSWORD"],
                                float(args[3]) if len(args) == 4 else 4)
                print(f"restore window open for {args[1]}; application logins are off")
            elif cmd == "sequences":
                with _connect("PG_BACKUP_URL") as backup:
                    print("sequences set: " + (", ".join(restore_sequences(migrate, backup)) or "none"))
            elif cmd == "close" and args in ([], ["--abandon"]):
                close_window(admin, migrate, abandon=bool(args))
                print("restore window closed; application logins are on")
            else:
                print(__doc__, file=sys.stderr)
                return 2
    except Refused as exc:
        print(f"restore_window: refused: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
