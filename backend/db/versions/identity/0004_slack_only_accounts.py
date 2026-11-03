"""identity: accounts a Slack bot creates (kind 'slack', the Slack user and workspace they belong to) and a details note on activity"""
from alembic import op
from db.lib.run_sql import run_sql

revision = "identity_0004"
down_revision = "identity_0003"
branch_labels = None
depends_on = None
# a contract: older code reads kind 'slack' as an ordinary person (listed, invitable, able to sign in through SSO), so it must not run
# against a database that may hold such accounts
min_code_epoch = 4


def upgrade():
    run_sql(__file__)


def downgrade():
    op.execute("DELETE FROM identity.users WHERE kind = 'slack'")
    op.execute("ALTER TABLE identity.users DROP CONSTRAINT users_kind_check, "
               "DROP CONSTRAINT users_slack_is_not_admin, ADD CONSTRAINT users_kind_check CHECK (kind IN ('human', 'service')), "
               "DROP COLUMN slack_user_id, DROP COLUMN slack_team_id")
    op.execute("ALTER TABLE identity.user_activities DROP COLUMN details")
