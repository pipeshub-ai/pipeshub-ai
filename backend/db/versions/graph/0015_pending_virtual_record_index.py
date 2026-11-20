"""graph: an index on the virtual record a record is indexing into (the pending vrid), so the holder lookups can use an index on both of their arms"""
from alembic import op
from db.lib.ddl import ensure_partitioned_index

revision = "graph_0015"
down_revision = "graph_0014"
branch_labels = None
depends_on = None
older_code_compatible = "adds an index only; older code does not read the field and reads the same rows"

# records that carry a pending vrid are few: the partial predicate keeps the index tiny, and every holder query repeats it
INDEXES = [
    ("nodes_record_pending_vrid", "org_id, (doc->>'pendingVirtualRecordId')", "coll = 'records' AND doc ? 'pendingVirtualRecordId'"),
]


def upgrade():
    for name, cols, where in INDEXES:
        ensure_partitioned_index(op.get_bind(), "graph.nodes", name, cols, where=where, blocking=True)


def downgrade():
    for name, *_ in INDEXES:
        op.execute(f"DROP INDEX IF EXISTS graph.{name}")
