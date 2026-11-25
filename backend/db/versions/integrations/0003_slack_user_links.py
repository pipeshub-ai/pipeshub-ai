"""integrations: Slack account links"""
from alembic import op
from db.lib.run_sql import run_sql

revision = "integrations_0003"
down_revision = "integrations_0002"
branch_labels = None
depends_on = None


def upgrade():
    run_sql(__file__)


def downgrade():
    op.execute("DELETE FROM kernel.rls_registry WHERE rel = 'integrations.slack_user_links'::regclass")
    op.execute("DROP TABLE integrations.slack_user_links")
