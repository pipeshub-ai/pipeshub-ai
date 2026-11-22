"""kernel: tables without org data are registered as internal"""
from alembic import op
from db.lib.run_sql import run_sql

revision = "kernel_0006"
down_revision = "kernel_0005"
branch_labels = None
depends_on = None


def upgrade():
    run_sql(__file__)


def downgrade():
    op.execute("DELETE FROM kernel.rls_registry WHERE rel = 'platform.deployment_profile'::regclass")
