"""kernel: the vetted function framework's own functions, kernel.org_lineage and its extension point kernel.org_parent_ids"""
from alembic import op
from db.lib.run_sql import run_sql
from db.lib.vetted import run_vetted

revision = "kernel_0010"
down_revision = "kernel_0009"
branch_labels = None
depends_on = None


def upgrade():
    run_sql(__file__)
    run_vetted(__file__, "org_parent_ids", "org_lineage")


def downgrade():
    op.execute("DROP FUNCTION kernel.org_lineage(text), kernel.org_parent_ids(text)")
    op.execute("DELETE FROM kernel.function_registry WHERE proc::oid NOT IN (SELECT oid FROM pg_proc)")
