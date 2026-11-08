"""platform: operator start/finish functions for jobs and the running job's checkpoint function"""
from alembic import op
from db.lib.ddl import ensure_index
from db.lib.run_sql import run_sql
from db.lib.vetted import run_vetted

revision = "platform_0002"
down_revision = "platform_0001"
branch_labels = None
depends_on = None
older_code_compatible = "the unique index is on platform.jobs, which no earlier code writes (no application or jobs role holds a privilege on it, only the new functions write it)"


def upgrade():
    run_sql(__file__)                       # leaves the role as the owner of platform.jobs
    # one running job of a kind at a time: the start function checks, and this index settles two starts that race
    ensure_index(op.get_bind(), "platform.jobs_one_running_per_kind",
                 "CREATE UNIQUE INDEX jobs_one_running_per_kind ON platform.jobs (kind) WHERE status = 'running'")
    run_vetted(__file__, "job_start", "job_finish", "job_checkpoint")


def downgrade():
    op.execute("DROP INDEX IF EXISTS platform.jobs_one_running_per_kind")
    op.execute("DROP FUNCTION platform.job_start(text), platform.job_finish(bigint, text), platform.job_checkpoint(bigint, text)")
    op.execute("DELETE FROM kernel.function_registry WHERE proc::oid NOT IN (SELECT oid FROM pg_proc)")
    op.execute("REVOKE SELECT, INSERT, UPDATE ON platform.jobs FROM pipeshub_vetted_platform")
    op.execute("REVOKE USAGE ON SCHEMA platform FROM pipeshub_vetted_platform")
    op.execute("UPDATE kernel.rls_registry SET vetted_read = false, vetted_write = false WHERE rel = 'platform.jobs'::regclass")
