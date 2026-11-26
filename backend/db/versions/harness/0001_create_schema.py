"""harness: schema only (the agent harness's stores follow)"""
from alembic import op
from db.lib.run_sql import run_sql

revision = "harness_0001"
down_revision = None
branch_labels = ("harness",)
depends_on = "kernel_0011"


def upgrade():
    run_sql(__file__)


def downgrade():
    op.execute("DROP SCHEMA harness")
