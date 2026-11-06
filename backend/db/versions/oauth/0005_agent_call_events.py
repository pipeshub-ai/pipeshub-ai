"""oauth: the record of calls made with bearer tokens"""
from alembic import op
from db.lib.run_sql import run_sql

revision = "oauth_0005"
down_revision = "oauth_0004"
branch_labels = None
depends_on = None
older_code_compatible = "adds one new table with its own policy and grants; no existing table, column, privilege or function changes"


def upgrade():
    run_sql(__file__)


def downgrade():
    op.execute("DELETE FROM kernel.rls_registry WHERE rel = 'oauth.agent_call_events'::regclass")
    op.execute("DROP TABLE oauth.agent_call_events")
