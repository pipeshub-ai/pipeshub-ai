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
    # An upgrade that is checked runs as one transaction, which refuses CREATE INDEX CONCURRENTLY: each leaf is indexed in it, so a
    # graph that already holds rows has its writers wait for the build of that leaf (the store is opt-in, and the build reads one leaf).
    # shortcut: the leaf locks last until the migration commits, so on a large graph with live writers they wait for the whole upgrade;
    # no release holds a graph built before this revision. Prebuild the leaves CONCURRENTLY ahead of db.migrate for a later index on
    # graphs that hold rows.
    for name, cols, where in INDEXES:
        ensure_partitioned_index(op.get_bind(), "graph.nodes", name, cols, where=where, blocking=True)


def downgrade():
    op.execute("DROP INDEX IF EXISTS graph.nodes_record_virtual_rev")
