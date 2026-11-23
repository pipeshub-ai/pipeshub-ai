"""graph: the vetted read, with its two partial indexes, of the records and files that link a storage document (a knowledge-base push that inlines stored images)"""
from alembic import op
from db.lib.ddl import ensure_partitioned_index
from db.lib.vetted import run_vetted

revision = "graph_0013"
down_revision = "graph_0012"
branch_labels = None
depends_on = None
older_code_compatible = "adds indexes and functions only; older code does not call them and reads the same rows"

FUNCTIONS = {"ref_links": "text[], text, text, int"}
# (name, columns, predicate): records by external id whatever their connector (nodes_record_external leads with the connector), files by path
INDEXES = [
    ("nodes_record_external_any", "org_id, (doc->>'externalRecordId')", "coll = 'records'"),
    ("nodes_file_path", "org_id, (doc->>'path')", "coll = 'files'"),
]


def upgrade():
    # Built inside the upgrade's one transaction, so the writers of a leaf wait for its build (as graph_0006 does).
    # shortcut: a build reads a whole leaf, with writers blocked: run this upgrade on a graph that holds many records in a maintenance window.
    for name, cols, where in INDEXES:
        ensure_partitioned_index(op.get_bind(), "graph.nodes", name, cols, where=where, blocking=True)
    run_vetted(__file__, *FUNCTIONS)


def downgrade():
    for name, args in FUNCTIONS.items():
        op.execute(f"DROP FUNCTION IF EXISTS graph.{name}({args})")
    op.execute("DELETE FROM kernel.function_registry WHERE proc::oid NOT IN (SELECT oid FROM pg_proc)")
    for name, *_ in INDEXES:
        op.execute(f"DROP INDEX IF EXISTS graph.{name}")
