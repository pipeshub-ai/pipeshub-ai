"""content: stored-file metadata (documents) and projects with their members"""
from alembic import op
from db.lib.run_sql import run_sql

revision = "content_0002"
down_revision = "content_0001"
branch_labels = None
depends_on = None
older_code_compatible = "creates new tables and their partitions; the dynamic statements only name objects it creates"


def upgrade():
    run_sql(__file__)


def downgrade():
    op.execute("DROP TABLE content.project_members, content.projects, content.documents")
    op.execute("DELETE FROM kernel.rls_registry WHERE rel::oid NOT IN (SELECT oid FROM pg_class)")
    op.execute("DROP DOMAIN content.oid24")
