"""oauth: agent registrations (the auth.md service_auth claim ceremony)"""
from alembic import op
from db.lib.run_sql import run_sql

revision = "oauth_0004"
down_revision = "oauth_0003"
branch_labels = None
depends_on = ("platform_data_0003",)
older_code_compatible = "adds a table no older code reads or writes, with its own import grants and change-log trigger; no existing table, column, privilege or function changes"


def upgrade():
    run_sql(__file__)
    op.execute("CREATE TRIGGER docstore_change_log AFTER INSERT OR UPDATE OR DELETE ON oauth.agent_registrations "
               "FOR EACH ROW EXECUTE FUNCTION platform.docstore_log_change('id', 'oauth.agent_registrations')")


def downgrade():
    op.execute("DROP TRIGGER IF EXISTS docstore_change_log ON oauth.agent_registrations")
    op.execute("DELETE FROM kernel.rls_registry WHERE rel = 'oauth.agent_registrations'::regclass")
    op.execute("DROP TABLE oauth.agent_registrations")
