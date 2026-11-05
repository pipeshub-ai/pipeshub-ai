"""retrieval: created whole: the catalog, the org-scoped tables, placement requests, postings and space checks, then the vetted functions"""
from alembic import op
from db.lib.run_sql import run_sql
from db.lib.vetted import run_vetted

revision = "retrieval_0001"
down_revision = None
branch_labels = ("retrieval",)
depends_on = "kernel_0011"
older_code_compatible = "creates a schema of its own (tables and partitions the dynamic statements create); older code does not use it"

FUNCTIONS = ("collection_get", "collection_list", "org_space_list", "collection_create", "collection_mark_deleting", "org_space_adopt",
             "org_leaf_request", "org_leaf_status", "collection_stamp_get", "collection_stamp_set", "vrid_orgs", "search_sparse",
             "org_space_replace_empty", "org_space_named_by", "org_space_name")


def upgrade():
    run_sql(__file__)
    run_vetted(__file__, *FUNCTIONS)


def downgrade():
    op.execute("DELETE FROM kernel.lock_keys WHERE class_id IN (7361, 7362, 7363, 7370)")
    op.execute("DELETE FROM kernel.rls_registry WHERE rel::oid IN (SELECT oid FROM pg_class WHERE relnamespace = 'retrieval'::regnamespace)")
    op.execute("DROP SCHEMA retrieval CASCADE")
    op.execute("DELETE FROM kernel.function_registry WHERE proc::oid NOT IN (SELECT oid FROM pg_proc)")
    op.execute("DELETE FROM kernel.rls_registry WHERE rel::oid NOT IN (SELECT oid FROM pg_class)")
