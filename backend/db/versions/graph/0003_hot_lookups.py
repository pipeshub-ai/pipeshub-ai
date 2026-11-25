"""graph: vetted reads of the code graph's hot lookups (blocks by record and by name, code files by path), which row-level security cannot serve from their indexes"""
from alembic import op
from db.lib.vetted import run_vetted

revision = "graph_0003"
down_revision = "graph_0002"
branch_labels = None
depends_on = None
older_code_compatible = "adds functions only; older code does not call them and reads the same rows"

FUNCTIONS = ("blocks_by_record", "blocks_by_name", "code_files_by_path")


def upgrade():
    run_vetted(__file__, *FUNCTIONS)


def downgrade():
    for name in FUNCTIONS:
        op.execute(f"DROP FUNCTION IF EXISTS graph.{name}(text, text, text[], jsonb[], boolean, text, int)")      # blocks_by_name has one more text first
        op.execute(f"DROP FUNCTION IF EXISTS graph.{name}(text, text[], jsonb[], boolean, text, int)")
    op.execute("DELETE FROM kernel.function_registry WHERE proc::oid NOT IN (SELECT oid FROM pg_proc)")
