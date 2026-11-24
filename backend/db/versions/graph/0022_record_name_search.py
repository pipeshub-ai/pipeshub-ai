"""graph: the knowledge hub's name search finds a connector's records and record groups by a trigram index of their names (graph.records_by_name)"""
from alembic import op
from db.lib.ddl import ensure_partitioned_index
from db.lib.vetted import run_vetted

revision = "graph_0022"
down_revision = "graph_0021"
branch_labels = None
depends_on = None
older_code_compatible = "adds an index and a function only; older code does not call the function and reads the same rows"

FUNCTIONS = {"records_by_name": "text, text, int"}
# Every name field the typed graph may list a node by (kg/model.py record_row: name, else recordName, else groupName), in one text, so a name a
# search keeps holds its pieces here whichever field it came from; graph.records_by_name spells it alike so the planner matches it.
NAME = "(coalesce(doc->>'name', '') || ' ' || coalesce(doc->>'recordName', '') || ' ' || coalesce(doc->>'groupName', ''))"
# The org and the connector are keys of the same GIN index (btree_gin), so a lookup reads the connector's matches, never another org's or connector's.
ONLINE_INDEXES = [
    {"table": "graph.nodes", "name": "nodes_record_name_trgm", "cols": f"org_id, (doc->>'connectorId'), {NAME} ext.gin_trgm_ops",
     "where": "coll IN ('records', 'recordGroups')", "using": "gin"},
]


def upgrade():
    # both extensions ship with PostgreSQL and are trusted; an administrator may have created them already (bootstrap, conversation_0002, graph_0006)
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA ext")
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gin WITH SCHEMA ext")
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
