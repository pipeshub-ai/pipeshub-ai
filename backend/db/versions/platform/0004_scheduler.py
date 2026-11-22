"""platform: the job scheduler tables (jobs, repeat entries) for deployments without Redis"""
from alembic import op
from db.lib.run_sql import run_sql

revision = "platform_0004"
down_revision = "platform_0003"
branch_labels = None
depends_on = None
older_code_compatible = "only new tables that no earlier code reads or writes; nothing existing is changed"


def upgrade():
    run_sql(__file__)


def downgrade():
    op.execute("DELETE FROM kernel.rls_registry WHERE rel IN ('platform.sched_jobs'::regclass, 'platform.sched_repeats'::regclass)")
    op.execute("DROP TABLE platform.sched_repeats, platform.sched_jobs")
