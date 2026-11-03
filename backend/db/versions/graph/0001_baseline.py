"""graph: the PostgreSQL graph store created whole: schema, nodes and edges, indexes, row policies, then the vetted cross-org reads"""
from alembic import op
from db.lib.ddl import ensure_partitioned_index
from db.lib.run_sql import run_sql
from db.lib.vetted import run_vetted

revision = "graph_0001"
down_revision = None
branch_labels = ("graph",)
depends_on = "kernel_0011"
older_code_compatible = "creates a schema of its own (tables and partitions the dynamic statements create); older code does not use it"

# The code graph reads blocks by the record they came from, by name, and code files by path prefix; the stage states by (virtual record, revision)
# and by (status, age). Entity reads: named entities by (kind, normKey prefix), value mentions by record, taxonomy nodes by normalized name,
# records by (connector, id) for the keyset pages of the entity index and the vector membership scans. The purge walk reads the org's trash
# oldest first. The cross-organisation reads (the vetted graph.system_*) list organisations by (org, id), connector instances by (id, org)
# and look one up by id; the user groups and roles a connector synced are read by (connector, external id). Partial per collection, so the other collections pay nothing.
INDEXES = [
    ("nodes_block_record", "org_id, (doc->>'recordId')", "coll = 'blocks'"),
    ("nodes_block_name", "org_id, (doc->>'connectorId'), (doc->>'nameFolded')", "coll = 'blocks'"),
    ("nodes_codefile_path", "org_id, (doc->>'filePath') text_pattern_ops", "coll = 'codeFiles'"),
    ("nodes_stage_revision", "org_id, (doc->>'virtualRecordId'), (doc->>'rev')", "coll = 'stageStates'"),
    ("nodes_stage_status", "org_id, (doc->>'status'), (doc->'updatedAtMs')", "coll = 'stageStates'"),
    ("nodes_ne_kind_key", "org_id, (doc->>'kind'), (doc->>'normKey') text_pattern_ops", "coll = 'namedEntities'"),
    ("nodes_value_record", "org_id, (doc->>'recordId')", "coll = 'valueMentions'"),
    ("nodes_taxonomy_norm", "org_id, coll, (doc->>'normalizedName')", "doc->>'normalizedName' IS NOT NULL"),
    ("nodes_record_connector_id", "org_id, (doc->>'connectorId'), id", "coll = 'records'"),
    ("nodes_org_by_id", "org_id, id", "coll = 'organizations'"),
    ("nodes_app_by_id", "id, org_id", "coll = 'apps'"),
    ("nodes_group_by_external", "org_id, (doc->>'connectorId'), (doc->>'externalGroupId')", "coll = 'groups'"),
    ("nodes_role_by_external", "org_id, (doc->>'connectorId'), (doc->>'externalRoleId')", "coll = 'roles'"),
    ("nodes_record_trash", "org_id, ((doc->>'deletedAtTimestamp')::bigint), id", "coll = 'records' AND (doc->>'isDeleted') = 'true'"),
]


def upgrade():
    run_sql(__file__)
    # shortcut: leaves that hold rows are indexed CONCURRENTLY, which an upgrade verified as one transaction refuses; this revision
    # runs on tables that have none (a fresh install). Index a populated graph with db.maintain before upgrading.
    for name, cols, where in INDEXES:
        ensure_partitioned_index(op.get_bind(), "graph.nodes", name, cols, where=where)
    run_vetted(__file__, "system_orgs", "system_apps", "system_app")


def downgrade():
    op.execute("DROP SCHEMA graph CASCADE")
    op.execute("DELETE FROM kernel.function_registry WHERE proc::oid NOT IN (SELECT oid FROM pg_proc)")
    op.execute("DELETE FROM kernel.rls_registry WHERE rel::oid NOT IN (SELECT oid FROM pg_class)")
