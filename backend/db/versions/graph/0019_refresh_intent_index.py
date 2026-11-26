"""graph: the partial index of the records that carry a Force reindex's refresh intent, for the intent sweep"""
from alembic import op
from db.lib.ddl import ensure_partitioned_index

revision = "graph_0019"
down_revision = "graph_0018"
branch_labels = None
depends_on = None
older_code_compatible = "adds an index only; older code never reads or writes the field it covers"


def upgrade():
    # Built inside the upgrade's one transaction, as graph_0012 does; the predicate keeps it to the few records with an intent.
    ensure_partitioned_index(op.get_bind(), "graph.nodes", "nodes_record_refresh_intent", "org_id, (id COLLATE ucs_basic)",
                             where="coll = 'records' AND doc->>'refreshIntentId' IS NOT NULL", blocking=True)


def downgrade():
    op.execute("DROP INDEX IF EXISTS graph.nodes_record_refresh_intent")
