"""kg: the indexes a browse of one node reads its children by"""
from alembic import op
from db.lib.ddl import ensure_partitioned_index

revision = "kg_0010"
down_revision = "kg_0009"
branch_labels = None
depends_on = None
older_code_compatible = "adds indexes only; an index changes no answer older code reads"

# The children of one node in the name and the updated sort orders (both directions), for a browse of a node with more children than a browse holds in memory (placeholders
# included: a browse lists them on the way to something readable). The name key is the bounded prefix the listing indexes of kg_0009 key on.
CHILD = "parent_sk IS NOT NULL AND kind IN (2, 3) AND NOT deleted AND NOT alien"
SORT_KEYS = {"name": "substring(name_sort FROM 1 FOR 1024)", "updated": "updated_ms"}
INDEXES = [(f"records_children_{k}_{d}", f'org_id, parent_sk, {col} {d.upper()} NULLS LAST, (id COLLATE "C")', CHILD) for k, col in SORT_KEYS.items() for d in ("asc", "desc")]


def upgrade():
    # shortcut: built inside the upgrade's one transaction with the writers of kg.records waiting (as kg_0009): run this upgrade on an install whose typed graph is large in a maintenance window.
    for name, cols, where in INDEXES:
        ensure_partitioned_index(op.get_bind(), "kg.records", name, cols, where=where, blocking=True)


def downgrade():
    for name, *_ in INDEXES:
        op.execute(f"DROP INDEX IF EXISTS kg.{name}")
