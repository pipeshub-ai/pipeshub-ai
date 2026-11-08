"""kernel: a skipped surface check is recorded"""
from alembic import op
from db.lib.run_sql import run_sql

revision = "kernel_0008"
down_revision = "kernel_0007"
branch_labels = None
depends_on = None


def upgrade():
    run_sql(__file__)


def downgrade():
    op.execute("DROP TABLE kernel.surface_skips")
