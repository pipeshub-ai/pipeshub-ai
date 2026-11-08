"""platform: the document store fence (store_epoch row, the prologue function that takes the shared barrier lock, and the boot reader)"""
from alembic import op
from db.lib.run_sql import run_sql
from db.lib.vetted import run_vetted

revision = "platform_0001"
down_revision = None
branch_labels = ("platform",)
depends_on = "kernel_0011"


def upgrade():
    run_sql(__file__)
    run_vetted(__file__, "assert_store_epoch")


def downgrade():
    op.execute("DROP FUNCTION kernel.assert_store_epoch(int), kernel.postgres_store_epoch()")
    op.execute("DELETE FROM kernel.function_registry WHERE proc::oid NOT IN (SELECT oid FROM pg_proc)")
    op.execute("DELETE FROM kernel.vetted_extension_reads WHERE rel = 'platform.store_epoch'::regclass")
    op.execute("DELETE FROM kernel.rls_registry WHERE rel = 'platform.store_epoch'::regclass")
    op.execute("DROP TABLE platform.store_epoch")
