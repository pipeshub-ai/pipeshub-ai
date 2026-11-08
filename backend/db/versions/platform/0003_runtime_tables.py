"""platform: key-value entries and a work queue with consumer groups, for deployments without Redis"""
from alembic import op
from db.lib.run_sql import run_sql

revision = "platform_0003"
down_revision = "platform_0002"
branch_labels = None
depends_on = "kernel_0014"
older_code_compatible = "only new tables that no earlier code reads or writes; nothing existing is changed"


def upgrade():
    run_sql(__file__)


def downgrade():
    op.execute("DELETE FROM kernel.rls_registry WHERE rel IN ('platform.kv_entries'::regclass, 'platform.queue_messages'::regclass, "
               "'platform.queue_groups'::regclass, 'platform.queue_deliveries'::regclass)")
    op.execute("DROP TABLE platform.queue_deliveries, platform.queue_groups, platform.queue_messages, platform.kv_entries")
