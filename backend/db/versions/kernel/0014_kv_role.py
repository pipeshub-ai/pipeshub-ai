"""kernel: the narrow `kv` service role (key-value table and fence only) for the processes that just read configuration"""
from alembic import op
from db.lib.run_sql import run_sql

revision = "kernel_0014"
down_revision = "kernel_0013"
branch_labels = None
depends_on = None
older_code_compatible = "a new role and a new entry in the list of service roles; no existing grant, function or table changes"


def upgrade():
    run_sql(__file__)


def downgrade():
    # the role is in the default list of service roles after the upgrade and is created by the bootstrap; only its grants go
    op.execute("GRANT pipeshub_vetted_kernel TO pipeshub_owner WITH SET TRUE")
    op.execute("SET LOCAL ROLE pipeshub_vetted_kernel")
    op.execute("REVOKE EXECUTE ON FUNCTION kernel.assert_profile_epoch(int), kernel.profile_epoch() FROM pipeshub_kv")
    op.execute("SET LOCAL ROLE pipeshub_owner")
    op.execute("REVOKE pipeshub_vetted_kernel FROM pipeshub_owner")
