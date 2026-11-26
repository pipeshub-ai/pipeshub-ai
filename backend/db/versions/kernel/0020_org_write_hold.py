"""kernel: the write fences take a per-org barrier and refuse the writes of an org that kernel.org_writes_held holds (an extension point; none here)"""
from alembic import op
from db.lib.vetted import run_vetted

revision = "kernel_0020"
down_revision = "kernel_0019"
branch_labels = None
depends_on = "platform_0001"     # replaces the body of platform_0001's document store fence
older_code_compatible = "the fences keep their signatures and, with no org's writes held, answer as before"

# the fences' bodies before this revision (kernel_0011's profile fence, platform_0001's store fence)
_STORE_FENCE_BEFORE = """CREATE OR REPLACE FUNCTION kernel.assert_store_epoch(p_epoch int) RETURNS void
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, kernel, pg_temp SET standard_conforming_strings = on AS $fn$
DECLARE r platform.store_epoch;
BEGIN
  IF current_setting('transaction_isolation') <> 'read committed' THEN
    RAISE EXCEPTION 'write units of work run at READ COMMITTED' USING ERRCODE = '25000';
  END IF;
  IF NOT pg_try_advisory_xact_lock_shared(7340, 1) THEN
    RAISE EXCEPTION 'the document store is frozen' USING ERRCODE = '25006';
  END IF;
  SELECT * INTO r FROM platform.store_epoch;
  IF r.backend IS DISTINCT FROM 'postgres' OR r.epoch IS DISTINCT FROM p_epoch THEN
    RAISE EXCEPTION 'the document store epoch does not match this process' USING ERRCODE = '55P04';
  END IF;
  IF r.readonly THEN
    RAISE EXCEPTION 'the document store is read-only' USING ERRCODE = '25006';
  END IF;
END $fn$"""


def upgrade():
    run_vetted(__file__, "org_writes_held", "org_write_hold")


def downgrade():
    run_vetted(__file__, "assert_profile_epoch")
    op.execute("GRANT pipeshub_vetted_kernel TO pipeshub_owner WITH SET TRUE")
    op.execute("GRANT CREATE ON SCHEMA kernel TO pipeshub_vetted_kernel")
    op.execute("SET LOCAL ROLE pipeshub_vetted_kernel")
    op.execute(_STORE_FENCE_BEFORE)
    op.execute("SET LOCAL ROLE pipeshub_owner")
    op.execute("REVOKE CREATE ON SCHEMA kernel FROM pipeshub_vetted_kernel")
    op.execute("REVOKE pipeshub_vetted_kernel FROM pipeshub_owner")
    op.execute("DROP FUNCTION kernel.org_writes_held(text)")
    op.execute("DELETE FROM kernel.lock_keys WHERE class_id = 7342")
    op.execute("DELETE FROM kernel.function_registry WHERE proc::oid NOT IN (SELECT oid FROM pg_proc)")
