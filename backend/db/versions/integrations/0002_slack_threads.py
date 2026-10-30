"""integrations: Slack thread conversations"""
from alembic import op
from db.lib.run_sql import run_sql

revision = "integrations_0002"
down_revision = "integrations_0001"
branch_labels = None
depends_on = None


def upgrade():
    run_sql(__file__)


def downgrade():
    op.execute("DELETE FROM kernel.rls_registry WHERE rel = 'integrations.slack_threads'::regclass")
    op.execute("DROP TABLE integrations.slack_threads")
