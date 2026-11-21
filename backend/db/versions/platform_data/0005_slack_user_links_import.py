"""platform data: the import login may copy the Slack account links (docstore-migrate, R40SLACK-FU-1)"""
from alembic import op
from db.lib.run_sql import run_sql

revision = "platform_data_0005"
down_revision = "platform_data_0004"
branch_labels = None
depends_on = ("integrations_0003",)
older_code_compatible = "adds the import role's policy and privileges on one table; no table, column or function changes, and no application role changes"


def upgrade():
    run_sql(__file__)


def downgrade():
    op.execute("DROP POLICY IF EXISTS import_all ON integrations.slack_user_links")
    op.execute("UPDATE kernel.rls_registry SET import_access = false WHERE rel = 'integrations.slack_user_links'::regclass")
    op.execute("REVOKE ALL ON integrations.slack_user_links FROM pipeshub_import")
