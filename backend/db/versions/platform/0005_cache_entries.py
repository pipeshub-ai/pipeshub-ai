"""platform: the Node API's session and short-lived cache table, for deployments without Redis"""
from alembic import op
from db.lib.run_sql import run_sql

revision = "platform_0005"
down_revision = "platform_0004"
branch_labels = None
depends_on = None
older_code_compatible = "only a new table that no earlier code reads or writes; nothing existing is changed"


def upgrade():
    run_sql(__file__)


def downgrade():
    op.execute("DELETE FROM kernel.rls_registry WHERE rel = 'platform.cache_entries'::regclass")
    op.execute("DROP TABLE platform.cache_entries")
