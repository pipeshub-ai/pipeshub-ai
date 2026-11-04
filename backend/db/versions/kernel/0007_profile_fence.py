"""kernel: the profile fence is VOLATILE, asserts READ COMMITTED, and each service login may call it"""
from alembic import op
from db.lib.run_sql import run_sql

revision = "kernel_0007"
down_revision = "kernel_0006"
branch_labels = None
depends_on = None
older_code_compatible = "same signature and result; the stricter check refuses only a write unit running above READ COMMITTED, which no service uses (the unit of work is READ COMMITTED by design)"


def upgrade():
    run_sql(__file__)


def downgrade():
    op.execute("""CREATE OR REPLACE FUNCTION kernel.assert_profile_epoch(p_epoch int) RETURNS void
        LANGUAGE plpgsql STABLE SET search_path = pg_catalog, platform, pg_temp AS $$
        DECLARE v_epoch int;
        BEGIN
          SELECT epoch INTO v_epoch FROM platform.deployment_profile;
          IF v_epoch IS DISTINCT FROM p_epoch THEN
            RAISE EXCEPTION 'deployment profile epoch is %, this process expects %', v_epoch, p_epoch USING ERRCODE = '55000';
          END IF;
        END $$""")
    op.execute("DELETE FROM kernel.function_registry WHERE proc = 'kernel.assert_profile_epoch(integer)'::regprocedure")
