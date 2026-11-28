"""kg: an index of one connector's trashed records by delete batch, so the trash count reads each batch of the connector once, and a page tells a batch's later delete time
from the one it is read at, without reading the batch's records"""
from alembic import op
from db.lib.ddl import ensure_partitioned_index

revision = "kg_0029"
down_revision = "kg_0028"
branch_labels = None
depends_on = None
older_code_compatible = "adds an index only; an index changes no answer"

# kg_0019's records_trash predicate (``kg.trash._TRASHED``), keyed by batch then time: the next batch of the connector is one seek, and so is a record of the batch deleted later.
ONLINE_INDEXES = [
    {"table": "kg.records", "name": "records_trash_app_batch", "cols": "org_id, app_sk, delete_batch, deleted_ms",
     "where": "coll = 3 AND deleted AND deleted_ms IS NOT NULL AND delete_batch IS NOT NULL"},
]


def upgrade():
    # db.migrate builds ONLINE_INDEXES before this transaction without blocking writers; here they are found valid (a new install builds it, empty).
    # On an install behind kg_0019 the columns come from that pending revision: the pre-build is skipped and this builds it here, on columns that are new and empty.
    for ix in ONLINE_INDEXES:
        ensure_partitioned_index(op.get_bind(), **ix, blocking=True)


def downgrade():
    for ix in ONLINE_INDEXES:
        op.execute(f"DROP INDEX IF EXISTS kg.{ix['name']}")
