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
# (name, columns, predicate). The keys are md5 of the address: a pasted address can exceed the size of a btree row, and a record holding one could not be
# written. The site expression and predicate are graph.ticket_sites' own, written alike so the planner matches them (%% is the driver's escape of %).
INDEXES = [
    ("nodes_record_weburl", "org_id, md5(doc->>'webUrl')", "coll = 'records'"),
    ("nodes_record_ticket_site", "org_id, (doc->>'connectorName'), md5((regexp_match(doc->>'webUrl', '^(.*)[/]browse[/]'))[1])",
     "coll = 'records' AND (doc->>'recordType') = 'TICKET' AND (doc->>'webUrl') LIKE '%%/browse/%%' AND coalesce(doc->>'isDeleted', '') <> 'true'"),
]


def upgrade():
    # Built inside the upgrade's one transaction, so the writers of a leaf wait for its build (as graph_0006 does).
    # shortcut: a build reads a whole leaf, with writers blocked: run this upgrade on a graph that holds many records in a maintenance window.
    for name, cols, where in INDEXES:
        ensure_partitioned_index(op.get_bind(), "graph.nodes", name, cols, where=where, blocking=True)
    run_vetted(__file__, *FUNCTIONS)


def downgrade():
    for name, args in FUNCTIONS.items():
        op.execute(f"DROP FUNCTION IF EXISTS graph.{name}({args})")
    op.execute("DELETE FROM kernel.function_registry WHERE proc::oid NOT IN (SELECT oid FROM pg_proc)")
    for name, *_ in INDEXES:
        op.execute(f"DROP INDEX IF EXISTS graph.{name}")
