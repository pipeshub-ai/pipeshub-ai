"""Alembic environment: SQL-first (no models), sync psycopg 3, one transaction per revision.

Epochs: revision ids are ``<branch>_<NNNN>`` and NNNN is the branch epoch. The epoch is written to
``kernel.schema_epoch`` here, in the revision's own transaction, never by a revision. A module attribute
``min_code_epoch`` marks a contract revision (code built before it must not run).

On a database that already has revisions, one transaction covers every pending revision and the post-upgrade checks
(``cfg.attributes["verify"]``, run on this connection before COMMIT). A refused check rolls back the revisions and the
epochs together, so a run that exits 4 leaves the database as it found it. An empty database is built revision by
revision (every revision in one transaction would hold more locks than a default server has) and checked at the end.
"""
import re

from alembic import context
from sqlalchemy import create_engine, event, text
from sqlalchemy.pool import NullPool

from db.lib.ddl import refuse_autocommit
from db.migrate import KEEPALIVES

OWNER_ROLE = "pipeshub_owner"
REVISION_ID = re.compile(r"^(?P<branch>[a-z][a-z0-9_]*)_(?P<n>\d{4})$")


def _parse(revision: str) -> tuple[str, int]:
    m = REVISION_ID.match(revision)
    if not m:
        raise RuntimeError(f"revision id {revision!r} must be <branch>_<NNNN>")
    return m["branch"], int(m["n"])


def record_epoch(ctx, step, heads, run_args):
    if step.is_stamp:
        return
    branch, n = _parse(step.up_revision.revision)
    conn = ctx.connection
    if step.is_upgrade:
        min_code = getattr(step.up_revision.module, "min_code_epoch", 0)
        conn.execute(text("SELECT kernel.set_epoch(:b, :e, :m)"), {"b": branch, "e": n, "m": min_code})
    elif conn.execute(text("SELECT to_regclass('kernel.schema_epoch') IS NOT NULL")).scalar():
        # Dev-only downgrade: the epoch follows the schema actually present; min_code_epoch never
        # decreases, so a contract stays a contract.
        if n == 1:
            conn.execute(text("DELETE FROM kernel.schema_epoch WHERE branch = :b"), {"b": branch})
        else:
            conn.execute(text("UPDATE kernel.schema_epoch SET epoch = :e, updated_at = now() WHERE branch = :b"),
                         {"b": branch, "e": n - 1})


def run() -> None:
    cfg = context.config
    engine = create_engine(cfg.attributes["url"], poolclass=NullPool,
                           connect_args={
        # client_connection_check_interval: a statement still running after the runner was killed is
        # cancelled within seconds instead of finishing (and holding its locks) up to statement_timeout.
        "options": "-c lock_timeout=5s -c statement_timeout=15min -c idle_in_transaction_session_timeout=10min "
                   "-c client_connection_check_interval=5s",
        **KEEPALIVES,
    })

    @event.listens_for(engine, "begin")
    def _as_owner(conn):
        # Objects are owned by the owner role, whoever logs in. Absent on a bare database, where
        # kernel_0001 creates it and switches itself.
        conn.exec_driver_sql(
            f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{OWNER_ROLE}') "
            f"THEN SET LOCAL ROLE {OWNER_ROLE}; END IF; END $$")

    with engine.connect() as connection:
        verify = cfg.attributes.get("verify")
        # Only an upgrade is atomic with its checks; a development downgrade of many modules in one transaction would run the
        # shared lock table out of memory.
        context.configure(connection=connection, target_metadata=None, version_table_schema="kernel",
                          transaction_per_migration=verify is None, on_version_apply=record_epoch)
        if verify:
            context.get_context().autocommit_block = refuse_autocommit
        with context.begin_transaction():
            context.run_migrations()
            if verify:
                connection.exec_driver_sql("RESET ROLE")             # the checks read the catalog as the login, not as the owner
                verify(connection.connection.driver_connection)


run()
