"""graph: the cross-organisation reads (system_orgs, system_apps, system_app) answer no rows to a unit bound to an organisation"""
from alembic import op
from db.lib.vetted import run_vetted

revision = "graph_0024"
down_revision = "graph_0023"
branch_labels = None
depends_on = None
older_code_compatible = "replaces three function bodies with ones that answer alike, or with no rows when app.org_id is set; older code calls them with the same arguments"

FUNCTIONS = {"system_orgs": "boolean, boolean, text, text, int", "system_apps": "boolean, text, text, int", "system_app": "text"}


def upgrade():
    op.execute("DROP FUNCTION IF EXISTS graph.system_orgs(boolean, boolean, text, text, int)")
    op.execute("DROP FUNCTION IF EXISTS graph.system_apps(boolean, text, text, int)")
    op.execute("DROP FUNCTION IF EXISTS graph.system_app(text)")
    op.execute("DELETE FROM kernel.function_registry WHERE proc::oid NOT IN (SELECT oid FROM pg_proc)")
    run_vetted(__file__, *FUNCTIONS)


def downgrade():
    pass      # the earlier bodies answer bound units with more rows; graph_0001 owns the functions and drops them on its own downgrade
