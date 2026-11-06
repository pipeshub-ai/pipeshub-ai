"""graph: vetted reads of the lookups the services make on every request or sync step (record by external id, user by userId, groups and roles by external id, records by virtual record id)"""
from alembic import op
from db.lib.vetted import run_vetted

revision = "graph_0004"
down_revision = "graph_0003"
branch_labels = None
depends_on = None
older_code_compatible = "adds functions only; older code does not call them and reads the same rows"

FUNCTIONS = {
    "record_by_external": "text, text", "user_by_user_id": "text", "user_group_by_external": "text, text", "record_group_by_external": "text, text",
    "app_role_by_external": "text, text", "records_by_virtual_id": "text[]",
}


def upgrade():
    run_vetted(__file__, *FUNCTIONS)


def downgrade():
    for name, args in FUNCTIONS.items():
        op.execute(f"DROP FUNCTION IF EXISTS graph.{name}({args})")
    op.execute("DELETE FROM kernel.function_registry WHERE proc::oid NOT IN (SELECT oid FROM pg_proc)")
