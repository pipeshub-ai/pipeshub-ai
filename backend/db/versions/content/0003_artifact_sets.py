"""content: the artifact set of a document (one content revision of a virtual record), unique per org, record and revision"""
from alembic import op
from db.lib.ddl import ensure_partitioned_index
from db.lib.run_sql import run_sql

revision = "content_0003"
down_revision = "content_0002"
branch_labels = None
depends_on = None
older_code_compatible = "adds a nullable column and an index; older code neither names nor needs them"


def upgrade():
    run_sql(__file__)
    # Two presigns of one revision racing each other settle on one row; the first column also serves listing and purging a record's revisions.
    with op.get_context().autocommit_block():   # CREATE INDEX CONCURRENTLY cannot run in a transaction
        ensure_partitioned_index(op.get_bind(), "content.documents", "documents_artifact_set",
                                 "org_id, (artifact_set->>'virtualRecordId'), (artifact_set->>'rev')",
                                 unique=True, where="artifact_set IS NOT NULL")


def downgrade():
    op.execute("DROP INDEX IF EXISTS content.documents_artifact_set")
    op.execute("ALTER TABLE content.documents DROP COLUMN artifact_set")
