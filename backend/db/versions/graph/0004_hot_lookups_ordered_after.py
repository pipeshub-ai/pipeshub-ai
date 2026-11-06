"""graph: the code graph's hot lookups find their matches by the key's index before ordering them (a plan that walks the primary key in id order filters the whole collection)"""
from alembic import op
from db.lib.vetted import run_vetted

revision = "graph_0004"
down_revision = "graph_0003"
branch_labels = None
depends_on = None
older_code_compatible = "replaces the bodies of three functions with ones that answer alike; older code calls them with the same arguments"

FUNCTIONS = {"blocks_by_record": "text, text[], jsonb[], boolean, text, int", "blocks_by_name": "text, text, text[], jsonb[], boolean, text, int",
             "code_files_by_path": "text, text[], jsonb[], boolean, text, int"}


def upgrade():
    for name, args in FUNCTIONS.items():
        op.execute(f"DROP FUNCTION IF EXISTS graph.{name}({args})")
    op.execute("DELETE FROM kernel.function_registry WHERE proc::oid NOT IN (SELECT oid FROM pg_proc)")
    run_vetted(__file__, *FUNCTIONS)


def downgrade():
    pass      # the earlier bodies answer alike; graph_0003 owns the functions and drops them on its own downgrade
