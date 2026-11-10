"""platform data: notifications, mail log, connectors config and the outbox (the schema and the store fence are branch platform)"""
from alembic import op
from db.lib.run_sql import run_sql

revision = "platform_data_0001"
down_revision = None
branch_labels = ("platform_data",)
depends_on = ("kernel_0011", "platform_0001")
older_code_compatible = "creates new tables and their partitions; the dynamic statements only name objects it creates"


def upgrade():
    run_sql(__file__)


def downgrade():
    op.execute("DROP TABLE platform.outbox, platform.connectors_config, platform.mail_log, platform.notifications")
    op.execute("DELETE FROM kernel.rls_registry WHERE rel::oid NOT IN (SELECT oid FROM pg_class)")
    op.execute("DROP DOMAIN platform.oid24")
