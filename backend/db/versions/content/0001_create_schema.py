"""content: schema only (the document store tables follow)"""
from alembic import op
from db.lib.run_sql import run_sql

revision = "content_0001"
down_revision = None
branch_labels = ("content",)
depends_on = "kernel_0011"


def upgrade():
    run_sql(__file__)


def downgrade():
    op.execute("DROP SCHEMA content")
