"""kernel: consumer inbox for replay protection"""
from alembic import op
from db.lib.run_sql import run_sql

revision = "kernel_0005"
down_revision = "kernel_0004"
branch_labels = None
depends_on = None


def upgrade():
    run_sql(__file__)


def downgrade():
    op.execute("DELETE FROM kernel.rls_registry WHERE rel = 'platform.consumer_inbox'::regclass")
    op.execute("DROP TABLE platform.consumer_inbox")
