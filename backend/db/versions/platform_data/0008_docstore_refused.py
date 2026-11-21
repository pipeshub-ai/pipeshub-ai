"""platform data: docstore-migrate's complete list of refused documents (R48MIGFIX-FU-2)"""
from alembic import op
from db.lib.run_sql import run_sql

revision = "platform_data_0008"
down_revision = "platform_data_0007"
branch_labels = None
depends_on = None
older_code_compatible = "adds a table only the import role reads and writes; no application role changes and no existing column or function changes"


def upgrade():
    run_sql(__file__)


def downgrade():
    op.execute("DROP TABLE platform.docstore_refused")
    op.execute("DELETE FROM kernel.rls_registry WHERE rel::oid NOT IN (SELECT oid FROM pg_class)")
