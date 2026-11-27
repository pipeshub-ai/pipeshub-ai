"""graph: the partial indexes of the flagged (external-collaborator) app memberships, by holder and by app, so a grant's lock of the memberships it keeps
reads the few flagged ones and never every membership of a user or of an app"""
from alembic import op
from db.lib.ddl import ensure_partitioned_index

revision = "graph_0024"
down_revision = "graph_0023"
branch_labels = None
depends_on = None
older_code_compatible = "adds indexes only; older code reads the same rows through the indexes it used"

# The predicate is the one the lock and the reaper write; a policy-bound statement still proves it (it is not an index condition), so the index serves them.
FLAGGED = "coll = 'userAppRelation' AND (doc->>'isExternalUser') = 'true'"
ONLINE_INDEXES = [
    {"table": "graph.edges", "name": "edges_external_by_holder", "cols": "org_id, from_coll, from_id, to_id", "where": FLAGGED},
    {"table": "graph.edges", "name": "edges_external_by_app", "cols": "org_id, to_id, from_coll, from_id", "where": FLAGGED},
]


def upgrade():
    # db.migrate builds ONLINE_INDEXES before this transaction without blocking writers; here they are found valid (a new install builds them, empty)
    for ix in ONLINE_INDEXES:
        ensure_partitioned_index(op.get_bind(), **ix, blocking=True)


def downgrade():
    for ix in ONLINE_INDEXES:
        op.execute(f"DROP INDEX IF EXISTS graph.{ix['name']}")
