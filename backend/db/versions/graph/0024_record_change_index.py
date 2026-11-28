"""graph: the vetted reads, with their index, of the records whose projected property values are out of date, for the property projection sweep"""
from alembic import op
from db.lib.ddl import ensure_partitioned_index
from db.lib.vetted import run_vetted

revision = "graph_0024"
down_revision = "graph_0023"
branch_labels = None
depends_on = None
older_code_compatible = "adds an index and two functions only; older code does not call them and reads the same rows"

FUNCTIONS = {"records_updated_since": "text, numeric, text, bigint, text, int", "records_needing_projection": "text, text, bigint, text, int"}
# The records that carry a numeric write time, by connector, then time and id: the change seek reads a page past its cursor from it. Built
# before the upgrade's transaction without blocking writers (db.migrate), so the sweep, which reads whether it is valid, finds it ready.
ONLINE_INDEXES = [
    {"table": "graph.nodes", "name": "nodes_record_change", "cols": "org_id, (doc->>'connectorId'), (doc->'updatedAtTimestamp'), (id COLLATE ucs_basic)",
     "where": "coll = 'records' AND jsonb_typeof(doc->'updatedAtTimestamp') = 'number'"},
]


def upgrade():
    # db.migrate builds ONLINE_INDEXES before this transaction without blocking writers; here they are found valid (a new install builds them, empty)
    for ix in ONLINE_INDEXES:
        ensure_partitioned_index(op.get_bind(), **ix, blocking=True)
    run_vetted(__file__, *FUNCTIONS)


def downgrade():
    for name, args in FUNCTIONS.items():
        op.execute(f"DROP FUNCTION IF EXISTS graph.{name}({args})")
    op.execute("DELETE FROM kernel.function_registry WHERE proc::oid NOT IN (SELECT oid FROM pg_proc)")
    for ix in ONLINE_INDEXES:
        op.execute(f"DROP INDEX IF EXISTS graph.{ix['name']}")
