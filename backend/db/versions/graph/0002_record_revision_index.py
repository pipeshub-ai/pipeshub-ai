"""graph: the index the stage-state joins read the record side by (virtual record, content revision)"""
from alembic import op
from db.lib.ddl import ensure_partitioned_index

revision = "graph_0002"
down_revision = "graph_0001"
branch_labels = None
depends_on = None
older_code_compatible = "adds an index only; older code reads and writes the same rows and plans without it"

# Which records hold a revision of a virtual record: the join of the stage states and of the unheld-revision sweep.
INDEXES = [
    ("nodes_record_virtual_rev", "org_id, (doc->>'virtualRecordId'), (doc->>'contentRev')", "coll = 'records'"),
]


def upgrade():
    # shortcut: as in graph_0001, a leaf that holds rows is indexed CONCURRENTLY, which an upgrade run as one transaction refuses;
    # index a populated graph with db.maintain before upgrading.
    for name, cols, where in INDEXES:
        ensure_partitioned_index(op.get_bind(), "graph.nodes", name, cols, where=where)


def downgrade():
    op.execute("DROP INDEX IF EXISTS graph.nodes_record_virtual_rev")
