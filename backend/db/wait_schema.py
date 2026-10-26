"""Wait until the database schema is at the head of this build: ``python -m db.wait_schema``.

For an init container of the application pods in a deployment where the migrate Job runs beside the rollout (the Helm chart's built-in
server): the pod does not start its services before every branch of ``kernel.schema_epoch`` has reached the newest epoch of this
build's migrations. Reads as a service login (``PG_WAIT_URL``, falling back to ``PG_NODE_URL``; the pooler is fine) after switching to
that login's service role, the way the application's units of work do. ``PG_WAIT_TIMEOUT_S`` (default 3600) bounds the wait; the exit
code is 0 when ready, 1 on timeout, 2 on bad configuration.
"""
import os
import re
import sys
import time

import psycopg
import psycopg.conninfo
import psycopg.sql

from db.schema_requirements import heads

_LOGIN = re.compile(r"^pipeshub_(node|connectors|indexing|query)_login$")


def log(message: str, *, error: bool = False) -> None:
    (sys.stderr if error else sys.stdout).write(f"wait_schema: {message}\n")
    sys.stdout.flush()


def behind(rows: dict[str, int], wanted: dict[str, int]) -> list[str]:
    """The branches whose epoch in the database is below the newest one of this build."""
    return [f"{b} {rows.get(b, 0)} < {n}" for b, n in sorted(wanted.items()) if rows.get(b, 0) < n]


# Errors that waiting does not cure: authentication, an unknown database, a missing privilege. Anything else (the server or the
# pooler not answering yet, the kernel schema not created yet) is "not ready yet" and the wait goes on.
_PERMANENT_STATES = ("28", "3D000", "42501")


def reason(exc: psycopg.Error) -> str:
    return (str(exc).strip().splitlines() or [type(exc).__name__])[0]


# A refused connection carries no SQLSTATE in psycopg, only the server's FATAL text.
_PERMANENT_TEXT = re.compile(r'authentication failed|no pg_hba\.conf entry|(database|role) "[^"]*" does not exist|permission denied')


def permanent(exc: psycopg.Error) -> bool:
    if "permission denied for schema kernel" in reason(exc):
        return False        # the schema exists but the first revision has not granted its USAGE yet: not ready, not wrong
    state = getattr(exc, "sqlstate", None) or ""
    return state.startswith(_PERMANENT_STATES[0]) or state in _PERMANENT_STATES[1:] or bool(_PERMANENT_TEXT.search(reason(exc)))


def read_epochs(url: str) -> dict[str, int]:
    try:
        user = psycopg.conninfo.conninfo_to_dict(url).get("user", "")
    except psycopg.ProgrammingError as exc:
        raise ValueError(f"PG_WAIT_URL is not a valid connection string: {reason(exc)}") from exc
    if not (m := _LOGIN.match(user)):
        raise ValueError("PG_WAIT_URL must be a pipeshub_<service>_login URL")
    with psycopg.connect(url, autocommit=False, prepare_threshold=None, connect_timeout=5) as conn, conn.transaction():
        conn.execute(psycopg.sql.SQL("SET LOCAL ROLE {}").format(psycopg.sql.Identifier(f"pipeshub_{m[1]}")))
        return dict(conn.execute("SELECT branch, epoch FROM kernel.schema_epoch").fetchall())


def main() -> int:
    url = (os.environ.get("PG_WAIT_URL") or os.environ.get("PG_NODE_URL") or "").replace("postgresql+psycopg://", "postgresql://")
    if not url:
        log("PG_WAIT_URL is not set", error=True)
        return 2
    wanted = heads()
    try:
        timeout = float(os.environ.get("PG_WAIT_TIMEOUT_S", "3600"))
    except ValueError:
        log("PG_WAIT_TIMEOUT_S must be a number of seconds", error=True)
        return 2
    deadline = time.monotonic() + timeout
    last = ""
    while True:
        try:
            missing = behind(read_epochs(url), wanted)
        except ValueError as exc:
            log(str(exc), error=True)
            return 2
        except (psycopg.errors.UndefinedTable, psycopg.errors.InvalidSchemaName):
            missing = ["the kernel schema does not exist yet (the migration has not run)"]
        except psycopg.Error as exc:
            if permanent(exc):
                log(f"cannot read the schema version, and waiting will not help: {reason(exc)}", error=True)
                return 2
            missing = [f"the database is not reachable yet: {reason(exc)}"]
        if not missing:
            log("the schema is at head")
            return 0
        if (msg := "; ".join(missing)) != last:
            log(f"waiting for the migration: {msg}")
            last = msg
        if time.monotonic() >= deadline:
            log("timed out", error=True)
            return 1
        time.sleep(3)


if __name__ == "__main__":
    sys.exit(main())
