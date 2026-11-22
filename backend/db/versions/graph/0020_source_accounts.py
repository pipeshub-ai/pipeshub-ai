"""graph: the partial indexes of a connector's source accounts, by address (linking an account to the user with that address) and by connector (deleting a connector)"""
from alembic import op
from db.lib.ddl import ensure_partitioned_index

revision = "graph_0020"
down_revision = "graph_0019"
branch_labels = None
depends_on = None
older_code_compatible = "adds indexes only; older code writes no source account and reads the same rows"

# (name, columns, predicate). The id of a source account is derived from (org, connector, source user id), so the primary key keeps it unique.
INDEXES = [
    ("nodes_source_account_email", "org_id, (doc->>'email')", "coll = 'sourceAccounts'"),
    ("nodes_source_account_connector", "org_id, (doc->>'connectorId')", "coll = 'sourceAccounts'"),
]


def upgrade():
    # Built inside the upgrade's one transaction, as graph_0013 does. No release wrote a source account before this revision, so neither index
    # gets a row, but each build still reads its whole leaf while the leaf's writers wait.
    for name, cols, where in INDEXES:
        ensure_partitioned_index(op.get_bind(), "graph.nodes", name, cols, where=where, blocking=True)


def downgrade():
    for name, *_ in INDEXES:
        op.execute(f"DROP INDEX IF EXISTS graph.{name}")
