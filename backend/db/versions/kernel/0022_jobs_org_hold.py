"""kernel: kernel.assert_org_writable, the write fence of the maintenance jobs' per-org steps (refuses an org kernel.org_writes_held holds)"""
from alembic import op
from db.lib.vetted import run_vetted

revision = "kernel_0022"
down_revision = "kernel_0021"
branch_labels = None
depends_on = None
older_code_compatible = "a new function no earlier code calls"


def upgrade() -> None:
    run_vetted(__file__, "assert_org_writable")


def downgrade() -> None:
    op.execute("DROP FUNCTION kernel.assert_org_writable(text)")
    op.execute("DELETE FROM kernel.function_registry WHERE proc::oid NOT IN (SELECT oid FROM pg_proc)")
