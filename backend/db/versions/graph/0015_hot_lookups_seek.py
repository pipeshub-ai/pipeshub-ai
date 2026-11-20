"""graph: the code graph's hot lookups seek a page past its cursor inside the key's index (each index now ends with id), so reading every match pages in O(matches)"""
from alembic import op
from db.lib.ddl import ensure_partitioned_index
from db.lib.vetted import run_vetted

revision = "graph_0015"
down_revision = "graph_0014"
branch_labels = None
depends_on = None
older_code_compatible = "replaces three indexes with ones that lead with the same columns and three function bodies with ones that answer alike; older code calls the same arguments"

FUNCTIONS = {"blocks_by_record": "text, text[], jsonb[], boolean, text, int", "blocks_by_name": "text, text, text[], jsonb[], boolean, text, int",
             "code_files_by_path": "text, text[], jsonb[], boolean, text, int"}
# The graph_0001 indexes with id appended: the matches of a key come in id order, so a page from a cursor reads n entries past it, not every match.
INDEXES = [
    ("nodes_block_record", "org_id, (doc->>'recordId'), id", "coll = 'blocks'"),
    ("nodes_block_name", "org_id, (doc->>'connectorId'), (doc->>'nameFolded'), id", "coll = 'blocks'"),
    ("nodes_codefile_path", "org_id, (doc->>'filePath') text_pattern_ops, id", "coll = 'codeFiles'"),     # text_pattern_ops: the code graph also reads a path prefix (codegraph.py)
]


def upgrade():
    for name, args in FUNCTIONS.items():
        op.execute(f"DROP FUNCTION IF EXISTS graph.{name}({args})")
    op.execute("DELETE FROM kernel.function_registry WHERE proc::oid NOT IN (SELECT oid FROM pg_proc)")
    # Built inside the upgrade's one transaction, so the writers of a leaf wait for its build (as graph_0006 does).
    # shortcut: a build reads a whole leaf, with writers blocked: run this upgrade on a graph that holds many blocks in a maintenance window.
    for name, cols, where in INDEXES:
        op.execute(f"DROP INDEX IF EXISTS graph.{name}")
        ensure_partitioned_index(op.get_bind(), "graph.nodes", name, cols, where=where, blocking=True)
    run_vetted(__file__, *FUNCTIONS)


def downgrade():
    pass      # the indexes still serve the earlier bodies (they lead with the same columns), which answer alike; graph_0001 and graph_0003 own them
