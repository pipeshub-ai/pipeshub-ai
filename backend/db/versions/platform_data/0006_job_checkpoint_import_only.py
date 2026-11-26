"""platform data: only the document-store copy (the import login) and the operator save a running job's progress; the jobs login no longer may"""
from alembic import op
from db.lib.run_sql import run_sql

revision = "platform_data_0006"
down_revision = "platform_data_0005"
branch_labels = None
depends_on = None
older_code_compatible = ("no earlier code saves job progress with the jobs login: the document-store copy, the only job that checkpoints, has saved it with "
                         "the import login since its first release (platform_data_0002), and the application's processes never call the function")


def upgrade():
    run_sql(__file__)


def downgrade():
    op.execute("GRANT pipeshub_vetted_platform TO pipeshub_owner WITH SET TRUE")
    op.execute("SET LOCAL ROLE pipeshub_vetted_platform")
    op.execute("REVOKE EXECUTE ON FUNCTION platform.job_checkpoint(bigint, text) FROM pipeshub_owner")
    op.execute("GRANT EXECUTE ON FUNCTION platform.job_checkpoint(bigint, text) TO pipeshub_jobs")
    op.execute("SET LOCAL ROLE pipeshub_owner")
    op.execute("REVOKE pipeshub_vetted_platform FROM pipeshub_owner")
