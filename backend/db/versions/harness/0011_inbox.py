"""harness: the Inbox's settings, run-notice claims and watch leases"""
from alembic import op
from db.lib.run_sql import run_sql

revision = "harness_0011"
down_revision = "harness_0010"
branch_labels = None
depends_on = None


def upgrade():
    run_sql(__file__)


def downgrade():
    op.execute(
        "DELETE FROM kernel.rls_registry WHERE rel IN "
        "('harness.run_watchers'::regclass, 'harness.run_end_notices'::regclass, 'harness.inbox_preferences'::regclass)"
    )
    op.execute("DROP TABLE harness.run_watchers")
    op.execute("DROP TABLE harness.run_end_notices")
    op.execute("DROP TABLE harness.inbox_preferences")
