"""platform: key-value access by key prefix per service role (row policies on platform.kv_entries), shadow by default"""
from alembic import op
from db.lib.run_sql import run_sql

revision = "platform_0006"
down_revision = "platform_0005"
branch_labels = None
depends_on = "kernel_0018"
older_code_compatible = ("the policies admit every access while platform.kv_access is in shadow mode, the default this revision installs, "
                         "so earlier code reads and writes the same rows with the same statements; only an operator turns enforce on")


def upgrade():
    run_sql(__file__)


def downgrade():
    op.execute("DELETE FROM kernel.internal_policies WHERE rel = 'platform.kv_entries'::regclass")
    op.execute("DROP POLICY kv_read ON platform.kv_entries")
    op.execute("DROP POLICY kv_insert ON platform.kv_entries")
    op.execute("DROP POLICY kv_update ON platform.kv_entries")
    op.execute("DROP POLICY kv_delete ON platform.kv_entries")
    op.execute("ALTER TABLE platform.kv_entries DISABLE ROW LEVEL SECURITY")
    op.execute("DROP FUNCTION platform.kv_may(text, boolean), platform.kv_admin_or_credential(text), platform.kv_outside(text, text, timestamptz)")
    op.execute("DELETE FROM kernel.rls_registry WHERE rel = 'platform.kv_access'::regclass")
    op.execute("DROP TABLE platform.kv_access")
