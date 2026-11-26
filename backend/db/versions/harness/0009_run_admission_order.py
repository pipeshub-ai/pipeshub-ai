"""harness: the order runs were admitted in"""
from alembic import op
from db.lib.ddl import ensure_index
from db.lib.run_sql import run_sql

revision = "harness_0009"
down_revision = "harness_0008"
branch_labels = None
depends_on = None
older_code_compatible = "adds an identity column the database fills on insert and a non-unique index; older code reads neither"


def upgrade():
    run_sql(__file__)
    # A session's runs newest first, a created_at tie broken by insertion order (harness_0003 leaves this index to here).
    ensure_index(op.get_bind(), "harness.runs_by_session",
                 "CREATE INDEX runs_by_session ON harness.runs (org_id, session_id, created_at, admission_order)")


def downgrade():
    op.execute("DROP INDEX harness.runs_by_session")
    op.execute("ALTER TABLE harness.runs DROP COLUMN admission_order")
