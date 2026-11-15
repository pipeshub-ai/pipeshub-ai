"""graph: the vetted read of a connector's declared record groups"""
from alembic import op
from db.lib.vetted import run_vetted

revision = "graph_0009"
down_revision = "graph_0008"
branch_labels = None
depends_on = None
older_code_compatible = "adds functions only; older code does not call them and reads the same rows"

FUNCTIONS = {"declared_record_groups": "text"}


def upgrade():
    run_vetted(__file__, *FUNCTIONS)


def downgrade():
    for name, args in FUNCTIONS.items():
        op.execute(f"DROP FUNCTION IF EXISTS graph.{name}({args})")
    op.execute("DELETE FROM kernel.function_registry WHERE proc::oid NOT IN (SELECT oid FROM pg_proc)")
