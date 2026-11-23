"""kg: an index of the listing rows kg does not decide, so a listing finds them without reading every row of the connector"""
from alembic import op
from db.lib.ddl import ensure_partitioned_index

revision = "kg_0020"
down_revision = "kg_0019"
branch_labels = None
depends_on = None
older_code_compatible = "adds an index only; an index changes no answer"

# The live listing rows (kg_0009's predicate) that kg does not decide (``kg.listing.DECIDED``): the few a listing hands to the access check. Spelt as the listing spells them, so
# that the predicate of its statement implies this one.
INDEXES = [
    ("records_undecided", "org_id, app_sk, sk",
     "kind IN (2, 3) AND NOT deleted AND NOT placeholder AND NOT alien AND NOT (r13 IS FALSE AND path IS NOT NULL AND class_id IS NOT NULL AND fence_job IS NULL)"),
]


def upgrade():
    # shortcut: built inside the upgrade's one transaction with the writers of kg.records waiting (as kg_0009): run this upgrade on an install whose typed graph is large in a maintenance window.
    for name, cols, where in INDEXES:
        ensure_partitioned_index(op.get_bind(), "kg.records", name, cols, where=where, blocking=True)


def downgrade():
    for name, *_ in INDEXES:
        op.execute(f"DROP INDEX IF EXISTS kg.{name}")
