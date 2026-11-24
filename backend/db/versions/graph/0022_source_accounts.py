"""graph: the partial indexes of a connector's source accounts, by address (linking an account to the user with that address) and by connector (deleting a connector)"""
from alembic import op
from db.lib.ddl import ensure_partitioned_index

revision = "graph_0022"
down_revision = "graph_0021"
branch_labels = None
depends_on = None
older_code_compatible = "adds indexes only; older code writes no source account and reads the same rows"

# The id of a source account is derived from (org, connector, source user id), so the primary key keeps it unique.
ONLINE_INDEXES = [
    {"table": "graph.nodes", "name": "nodes_source_account_email", "cols": "org_id, (doc->>'email')", "where": "coll = 'sourceAccounts'"},
    {"table": "graph.nodes", "name": "nodes_source_account_connector", "cols": "org_id, (doc->>'connectorId')", "where": "coll = 'sourceAccounts'"},
]


def upgrade():
    # db.migrate builds ONLINE_INDEXES before this transaction without blocking writers; here they are found valid (a new install builds them, empty)
    for ix in ONLINE_INDEXES:
        ensure_partitioned_index(op.get_bind(), **ix, blocking=True)


def downgrade():
    op.execute("DROP INDEX IF EXISTS graph.nodes_source_account_email, graph.nodes_source_account_connector")
