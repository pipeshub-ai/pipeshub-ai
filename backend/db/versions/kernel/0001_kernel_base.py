"""kernel: roles, schemas, schema epochs, job-completion gate"""
from alembic import op
from db.lib.run_sql import run_sql

revision = "kernel_0001"
down_revision = None
branch_labels = ("kernel",)
depends_on = None
older_code_compatible = "the first revision of the kernel: creates roles and schemas, nothing older exists to break"


def upgrade():
    run_sql(__file__)


def downgrade():
    # Development only. Roles and the kernel schema stay (alembic_version lives in it).
    op.execute("DROP TABLE kernel.schema_epoch, kernel.completed_jobs CASCADE")
    op.execute("DROP FUNCTION kernel.set_epoch(text, int, int), kernel.require_job_done(text), "
               "kernel.pg_major(), kernel.has_extension(text)")
