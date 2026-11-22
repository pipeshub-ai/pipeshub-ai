"""oauth: the advisory-lock class of the retention sweep of delegation events"""
from alembic import op
from db.lib.run_sql import run_sql

revision = "oauth_0003"
down_revision = "oauth_0002"
branch_labels = None
depends_on = None
older_code_compatible = "registers an advisory-lock class; no table, column, privilege or function changes"


def upgrade():
    run_sql(__file__)


def downgrade():
    op.execute("DELETE FROM kernel.lock_keys WHERE class_id = 7364")
