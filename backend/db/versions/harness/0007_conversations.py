"""harness: conversations stores (the fork requests; the conversations are the conversation branch's)"""
from alembic import op
from db.lib.run_sql import run_sql

revision = "harness_0007"
down_revision = "harness_0006"
branch_labels = None
depends_on = None


def upgrade():
    run_sql(__file__)


def downgrade():
    op.execute("DELETE FROM kernel.rls_registry WHERE rel = 'harness.session_forks'::regclass")
    op.execute("DROP TABLE harness.session_forks")
