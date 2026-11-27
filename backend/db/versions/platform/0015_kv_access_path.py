"""platform: the key-value access matrix checks the path platform.kv_access_path gives (the key itself by default)"""
from alembic import op
from db.lib.run_sql import run_sql

revision = "platform_0015"
down_revision = "platform_0014"
branch_labels = None
depends_on = None
older_code_compatible = "the default path is the key, so every role reads and writes exactly what it did"


def upgrade() -> None:
    run_sql(__file__)


def downgrade() -> None:
    """The policies of platform_0006 again (the matrix on the key itself), then the function."""
    op.execute("ALTER POLICY kv_read ON platform.kv_entries USING (platform.kv_may(key, false) OR platform.kv_outside(key, 'read', expires_at))")
    op.execute("ALTER POLICY kv_insert ON platform.kv_entries WITH CHECK (platform.kv_may(key, true) OR platform.kv_outside(key, 'insert', expires_at))")
    op.execute("ALTER POLICY kv_update ON platform.kv_entries USING (platform.kv_may(key, true) OR platform.kv_outside(key, 'update', expires_at)) "
               "WITH CHECK (platform.kv_may(key, true) OR platform.kv_outside(key, 'update', expires_at))")
    op.execute("ALTER POLICY kv_delete ON platform.kv_entries "
               "USING (platform.kv_may(key, true) OR expires_at <= now() OR platform.kv_outside(key, 'delete', expires_at))")
    for pol in ("kv_read", "kv_insert", "kv_update", "kv_delete"):
        op.execute(f"SELECT kernel.register_internal_policy('platform.kv_entries', '{pol}')")
    op.execute("DROP FUNCTION platform.kv_access_path(text)")
