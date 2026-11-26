"""graph: an index on the virtual record a record is indexing into (the pending vrid), so the holder lookups can use an index on both of their arms"""
from alembic import op
from db.lib.ddl import ensure_partitioned_index

revision = "graph_0015"
down_revision = "graph_0014"
branch_labels = None
depends_on = None
older_code_compatible = "adds an index only; older code does not read the field and reads the same rows"

# records that carry a pending vrid are few: the partial predicate keeps the index tiny, and every holder query repeats it
ONLINE_INDEXES = [
    {"table": "graph.nodes", "name": "nodes_record_pending_vrid", "cols": "org_id, (doc->>'pendingVirtualRecordId')",
     "where": "coll = 'records' AND doc ? 'pendingVirtualRecordId'"},
]


def upgrade():
    # db.migrate builds ONLINE_INDEXES before this transaction without blocking writers; here they are found valid (a new install builds them, empty)
    for ix in ONLINE_INDEXES:
        ensure_partitioned_index(op.get_bind(), **ix, blocking=True)


def downgrade():
    for ix in ONLINE_INDEXES:
        op.execute(f"DROP INDEX IF EXISTS graph.{ix['name']}")
