"""graph: the records at web addresses read through an index on the address, and the Jira sites of an org's tickets by one probe per site"""
from alembic import op
from db.lib.ddl import ensure_partitioned_index
from db.lib.vetted import run_vetted

revision = "graph_0017"
down_revision = "graph_0016"
branch_labels = None
depends_on = None
older_code_compatible = "adds indexes and functions only; older code does not call them and reads the same rows"

FUNCTIONS = {"records_by_weburl": "text[]", "ticket_sites": "text, text[], int"}
# The keys are md5 of the address: a pasted address can exceed the size of a btree row, and a record holding one could not be
# written. The site expression and predicate are graph.ticket_sites' own, written alike so the planner matches them (%% is the driver's escape of %).
ONLINE_INDEXES = [
    {"table": "graph.nodes", "name": "nodes_record_weburl", "cols": "org_id, md5(doc->>'webUrl')", "where": "coll = 'records'"},
    {"table": "graph.nodes", "name": "nodes_record_ticket_site", "cols": "org_id, (doc->>'connectorName'), md5((regexp_match(doc->>'webUrl', '^(.*)[/]browse[/]'))[1])",
     "where": "coll = 'records' AND (doc->>'recordType') = 'TICKET' AND (doc->>'webUrl') LIKE '%%/browse/%%' AND coalesce(doc->>'isDeleted', '') <> 'true'"},
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
