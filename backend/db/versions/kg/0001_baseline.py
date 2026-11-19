"""kg: the typed graph: schema, tables with their row policies, indexes"""
from alembic import op
from db.lib.ddl import ensure_partitioned_index
from db.lib.run_sql import run_sql

revision = "kg_0001"
down_revision = None
branch_labels = ("kg",)
depends_on = ("kernel_0017", "graph_0002")
older_code_compatible = "creates a schema of its own; older code never touches it"

INDEXES = [
    # (table, name, columns, unique[, partial predicate])
    ("kg.records", "records_id", "org_id, coll, id", True),               # a node's key; also what the projection looks a row up by
    ("kg.records", "records_app", "org_id, app_sk, sk", False),           # one connector's rows: verify, backfill, restamp
    ("kg.records", "records_parent", "org_id, parent_sk, sk", False),     # the children of a node
    ("kg.records", "records_group", "org_id, group_sk, sk", False),       # the members of a record group
    ("kg.links", "links_child", "org_id, child_sk, kind", False),         # a child's non-primary parents and groups
    ("kg.scope_members", "scope_members_by_node", "org_id, node_sk", False),
    ("kg.scope_members", "scope_members_by_app", "org_id, app_sk, node_sk", False),
    ("kg.principal_closure", "principal_closure_by_principal", "org_id, principal_sk, user_sk", False),
    ("kg.scope_classes", "scope_classes_hash", "org_id, hash", True),
    ("kg.scope_classes", "scope_classes_group", "org_id, group_sk", False),
    ("kg.records", "records_legacy", "org_id, app_sk, legacy_reason", False, "class_id IS NULL AND legacy_reason > 0"),   # the legacy share of a connector, by reason
    ("kg.graph_changes", "graph_changes_app", "org_id, app_id", False),   # a reader of a connector looks for pending changes of it
    ("kg.graph_changes", "graph_changes_txid", "org_id, txid", False),    # a flush takes its own transaction's rows; the drainer reads the oldest first
]


def upgrade():
    run_sql(__file__)
    # An upgrade that is checked runs as one transaction, which refuses CREATE INDEX CONCURRENTLY: the tables are new, so the plain build is instant.
    for table, name, cols, unique, *where in INDEXES:
        ensure_partitioned_index(op.get_bind(), table, name, cols, unique=unique, where=where[0] if where else "", blocking=True)


def downgrade():
    op.execute("DELETE FROM kernel.lock_keys WHERE class_id = 7360")
    op.execute("DROP SCHEMA kg CASCADE")
    op.execute("DELETE FROM kernel.function_registry WHERE proc::oid NOT IN (SELECT oid FROM pg_proc)")
    op.execute("DELETE FROM kernel.rls_registry WHERE rel::oid NOT IN (SELECT oid FROM pg_class)")
