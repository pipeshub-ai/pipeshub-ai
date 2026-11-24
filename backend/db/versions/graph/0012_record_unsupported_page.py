"""graph: the vetted read, with its partial index, of a connector's records whose file type was not supported, for the file-type re-queue"""
from alembic import op
from db.lib.ddl import ensure_partitioned_index
from db.lib.vetted import run_vetted

revision = "graph_0012"
down_revision = "graph_0011"
branch_labels = None
depends_on = None
older_code_compatible = "adds an index and a function only; older code does not call it and reads the same rows"

FUNCTIONS = {"records_unsupported_page": "text, text, int"}


def upgrade():
    # Built inside the upgrade's one transaction, as graph_0006 does; the predicate keeps it to the records at that status.
    ensure_partitioned_index(op.get_bind(), "graph.nodes", "nodes_record_unsupported", "org_id, (doc->>'connectorId'), (id COLLATE ucs_basic)",
                             where="coll = 'records' AND doc->>'indexingStatus' = 'FILE_TYPE_NOT_SUPPORTED'", blocking=True)
    run_vetted(__file__, *FUNCTIONS)


def downgrade():
    for name, args in FUNCTIONS.items():
        op.execute(f"DROP FUNCTION IF EXISTS graph.{name}({args})")
    op.execute("DELETE FROM kernel.function_registry WHERE proc::oid NOT IN (SELECT oid FROM pg_proc)")
    op.execute("DROP INDEX IF EXISTS graph.nodes_record_unsupported")
