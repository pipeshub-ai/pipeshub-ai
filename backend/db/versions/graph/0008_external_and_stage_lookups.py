"""graph: vetted reads of groups, record groups and roles by external id, and of the stage sweep's page of states and the records on their revisions"""
from alembic import op
from db.lib.ddl import ensure_partitioned_index
from db.lib.vetted import run_vetted

revision = "graph_0008"
down_revision = "graph_0007"
branch_labels = None
depends_on = None
older_code_compatible = "adds an index and functions only; older code does not call them and reads the same rows"

FUNCTIONS = {"node_by_external": "text, text, text", "stage_states_page": "text, text, text, text, int", "records_by_revision": "text[], text[]"}


def upgrade():
    # Built inside the upgrade's one transaction, as graph_0006 does (see its note on a graph that holds rows).
    ensure_partitioned_index(op.get_bind(), "graph.nodes", "nodes_stage_page", "org_id, (doc->>'connectorId'), (doc->>'stage'), (doc->>'status'), id", where="coll = 'stageStates'", blocking=True)
    run_vetted(__file__, *FUNCTIONS)


def downgrade():
    for name, args in FUNCTIONS.items():
        op.execute(f"DROP FUNCTION IF EXISTS graph.{name}({args})")
    op.execute("DELETE FROM kernel.function_registry WHERE proc::oid NOT IN (SELECT oid FROM pg_proc)")
    op.execute("DROP INDEX IF EXISTS graph.nodes_stage_page")
