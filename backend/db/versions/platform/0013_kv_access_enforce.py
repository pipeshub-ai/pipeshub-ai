"""platform: key-value access by prefix enforced by default (platform.kv_access.enforce = true)"""
from alembic import op
from db.lib.run_sql import run_sql

revision = "platform_0013"
down_revision = "platform_0012"
branch_labels = None
depends_on = None
older_code_compatible = (
    "the matrix is unchanged and grants every key the services of this and earlier releases read or write; an install that runs older "
    "code can keep shadow with PG_KV_ACCESS=shadow on the migrate job"
)


def upgrade():
    run_sql(__file__)


def downgrade():
    op.execute("UPDATE platform.kv_access SET enforce = false, updated_at = now()")
