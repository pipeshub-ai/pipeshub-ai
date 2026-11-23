"""identity: accounts a Slack bot creates (kind 'slack', the Slack user and workspace they belong to) and a details note on activity"""
from alembic import op
from db.lib.run_sql import run_sql

revision = "identity_0006"
down_revision = "identity_0005"
branch_labels = None
depends_on = None
# a contract: older code reads kind 'slack' as an ordinary person (listed, invitable, able to sign in through SSO), so it must not run
# against a database that may hold such accounts
min_code_epoch = 6


def upgrade():
    run_sql(__file__)


def downgrade():
    # The table forces row-level security, which hides every org's rows from the owner running this: without lifting it for the delete nothing is
    # removed and the check below refuses the rows that remain (as retrieval_0002 does).
    op.execute("ALTER TABLE identity.users NO FORCE ROW LEVEL SECURITY")
    op.execute("DELETE FROM identity.users WHERE kind = 'slack'")
    op.execute("ALTER TABLE identity.users FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE identity.users DROP CONSTRAINT users_kind_check, "
               "DROP CONSTRAINT users_slack_is_not_admin, ADD CONSTRAINT users_kind_check CHECK (kind IN ('human', 'service')), "
               "DROP COLUMN slack_user_id, DROP COLUMN slack_team_id")
    op.execute("ALTER TABLE identity.user_activities DROP COLUMN details")
