"""oauth: agent registrations (the auth.md service_auth claim ceremony)"""
from alembic import op
from db.lib.run_sql import run_sql

revision = "oauth_0004"
down_revision = "oauth_0003"
branch_labels = None
depends_on = None
older_code_compatible = "adds a table no older code reads or writes; no existing table, column, privilege or function changes"


def upgrade():
    run_sql(__file__)


def downgrade():
    op.execute("DELETE FROM kernel.rls_registry WHERE rel = 'oauth.agent_registrations'::regclass")
    op.execute("DROP TABLE oauth.agent_registrations")
