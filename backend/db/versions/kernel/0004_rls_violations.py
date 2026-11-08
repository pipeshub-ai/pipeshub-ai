"""kernel: the rls_violations catalog check (zero rows = pass)"""
from alembic import op
from db.lib.run_sql import run_sql

revision = "kernel_0004"
down_revision = "kernel_0003"
branch_labels = None
depends_on = None


def upgrade():
    run_sql(__file__)


def downgrade():
    op.execute("DROP VIEW kernel.rls_warnings, kernel.rls_violations")
