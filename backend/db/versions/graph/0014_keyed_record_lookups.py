"""graph: the records of virtual record ids read by one index probe per id, and a ticket found by its issue key through a trigram index on its address"""
from alembic import op
from db.lib.ddl import ensure_partitioned_index
from db.lib.vetted import run_vetted

revision = "graph_0014"
down_revision = "graph_0013"
branch_labels = None
depends_on = None
older_code_compatible = "replaces one function's body with one that answers alike and adds an index and a function; older code calls the same arguments and does not call the new one"

REPLACED = {"records_by_virtual_id": "text[]"}
FUNCTIONS = {"record_by_issue_url": "text, text"}
# (name, columns, predicate, access method): the address of each ticket record, by trigram, for a pattern match on the issue key
INDEXES = [
    ("nodes_record_ticket_url", "(doc->>'webUrl') ext.gin_trgm_ops", "coll = 'records' AND (doc->>'recordType') = 'TICKET'", "gin"),
]


def upgrade():
    for name, args in REPLACED.items():
        op.execute(f"DROP FUNCTION IF EXISTS graph.{name}({args})")
    op.execute("DELETE FROM kernel.function_registry WHERE proc::oid NOT IN (SELECT oid FROM pg_proc)")
    # Built inside the upgrade's one transaction, so the writers of a leaf wait for its build (as graph_0006 does).
    # shortcut: a build reads a whole leaf, with writers blocked: run this upgrade on a graph that holds many records in a maintenance window.
    for name, cols, where, using in INDEXES:
        ensure_partitioned_index(op.get_bind(), "graph.nodes", name, cols, where=where, blocking=True, using=using)
    run_vetted(__file__, *REPLACED, *FUNCTIONS)


def downgrade():
    # records_by_virtual_id keeps the body it has: the earlier one answers alike, and graph_0004 owns the function and drops it on its own downgrade
    for name, args in FUNCTIONS.items():
        op.execute(f"DROP FUNCTION IF EXISTS graph.{name}({args})")
    op.execute("DELETE FROM kernel.function_registry WHERE proc::oid NOT IN (SELECT oid FROM pg_proc)")
    for name, *_ in INDEXES:
        op.execute(f"DROP INDEX IF EXISTS graph.{name}")
