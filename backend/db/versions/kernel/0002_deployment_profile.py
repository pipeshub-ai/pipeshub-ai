"""kernel: deployment profile epoch and its fence function"""
from alembic import op
from db.lib.run_sql import run_sql

revision = "kernel_0002"
down_revision = "kernel_0001"
branch_labels = None
depends_on = None


def upgrade():
    run_sql(__file__)


def downgrade():
    op.execute("DROP FUNCTION kernel.assert_profile_epoch(int)")
    op.execute("DROP TABLE platform.deployment_profile")
