"""kg: two indexes of a node's live children, of the ones kg does not decide and by connector, so a browse finds a wide node's undecided children without reading all of them"""
from alembic import op
from db.lib.ddl import ensure_partitioned_index

revision = "kg_0030"
down_revision = "kg_0029"
branch_labels = None
depends_on = None
older_code_compatible = "adds indexes only; an index changes no answer"

# A browse's live children (``kg.listing.BROWSE_LIVE``, as the children indexes of kg_0009 spell it): the ones kg does not decide (a placeholder, or not ``kg.listing.DECIDED``:
# records_undecided's predicate by parent, and a child of no connector), and every one by its connector, then parent (a child of another connector than the node's is undecided
# too). records_child_app's predicate names app_sk, which no other read of children states, so it serves only a probe that asks one connector at a time, never a read of a
# node's children in a sort order (the children indexes do).
ONLINE_INDEXES = [
    {"table": "kg.records", "name": "records_child_undecided", "cols": "org_id, parent_sk, sk",
     "where": "parent_sk IS NOT NULL AND kind IN (2, 3) AND NOT deleted AND NOT alien "
              "AND (placeholder OR app_sk IS NULL OR NOT (r13 IS FALSE AND path IS NOT NULL AND class_id IS NOT NULL AND fence_job IS NULL))"},
    {"table": "kg.records", "name": "records_child_app", "cols": "org_id, app_sk, parent_sk",
     "where": "parent_sk IS NOT NULL AND kind IN (2, 3) AND NOT deleted AND NOT alien AND app_sk IS NOT NULL"},
]


def upgrade():
    # db.migrate builds ONLINE_INDEXES before this transaction without blocking writers; here they are found valid (a new install builds them, empty)
    for ix in ONLINE_INDEXES:
        ensure_partitioned_index(op.get_bind(), **ix, blocking=True)


def downgrade():
    for ix in ONLINE_INDEXES:
        op.execute(f"DROP INDEX IF EXISTS kg.{ix['name']}")
