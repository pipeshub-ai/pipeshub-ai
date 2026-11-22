"""kernel: person-level class, jobs-only import/sweep checks, dedupe exception, fence barrier, lock-key registry, sequence waivers"""
from alembic import op
from db.lib.run_sql import run_sql
from db.lib.vetted import run_vetted

revision = "kernel_0011"
down_revision = "kernel_0010"
branch_labels = None
depends_on = None
min_code_epoch = 11          # a contract: code before this revision reads platform.deployment_profile directly (boot, unit of work); the service roles lose SELECT on it


def upgrade():
    run_sql(__file__)
    run_vetted(__file__, "assert_profile_epoch", "profile_epoch")


def downgrade():
    op.execute("DROP FUNCTION kernel.profile_epoch()")
    op.execute("DELETE FROM kernel.function_registry WHERE proc::oid NOT IN (SELECT oid FROM pg_proc)")
    # the fence goes back to the owner role and to its 0007 body (0007's own downgrade then replaces that)
    op.execute("GRANT pipeshub_vetted_kernel TO pipeshub_owner WITH SET TRUE")
    op.execute("ALTER FUNCTION kernel.assert_profile_epoch(int) OWNER TO pipeshub_owner")
    op.execute("REVOKE pipeshub_vetted_kernel FROM pipeshub_owner")
    op.execute("""CREATE OR REPLACE FUNCTION kernel.assert_profile_epoch(p_epoch int) RETURNS void
LANGUAGE plpgsql VOLATILE SET search_path = pg_catalog, platform, pg_temp AS $$
DECLARE v_epoch int;
BEGIN
  IF current_setting('transaction_isolation') <> 'read committed' THEN
    RAISE EXCEPTION 'write units of work run at READ COMMITTED' USING ERRCODE = '25000';
  END IF;
  SELECT epoch INTO v_epoch FROM platform.deployment_profile;
  IF v_epoch IS DISTINCT FROM p_epoch THEN
    RAISE EXCEPTION 'deployment profile epoch is %, this process expects %', v_epoch, p_epoch USING ERRCODE = '55000';
  END IF;
END $$""")
    op.execute("DROP VIEW kernel.rls_violations")
    op.execute("""CREATE VIEW kernel.rls_violations WITH (security_invoker = true) AS
SELECT c.rule, c.object FROM kernel.rls_violations_core c
  WHERE NOT (c.rule = '42 vetted role pipeshub_vetted_kernel holds SELECT outside its schema' AND c.object IN (SELECT rel::text FROM kernel.vetted_extension_reads))
UNION ALL SELECT rule, object FROM kernel.vetted_violations""")
    op.execute("DROP VIEW kernel.person_violations")
    op.execute("DROP FUNCTION kernel.src_text(text, boolean)")
    op.execute("GRANT SELECT ON platform.deployment_profile TO pipeshub_node, pipeshub_connectors, pipeshub_indexing, pipeshub_query, pipeshub_jobs")
    op.execute("DELETE FROM kernel.vetted_extension_reads WHERE rel = 'platform.deployment_profile'::regclass")
    op.execute("DELETE FROM kernel.extension_schemas WHERE nsp = 'platform'")
    op.execute("REVOKE SELECT ON platform.deployment_profile FROM pipeshub_vetted_kernel")
    op.execute("REVOKE USAGE ON SCHEMA platform FROM pipeshub_vetted_kernel")
    op.execute("DROP FUNCTION kernel.grant_job_gate(name), kernel.waive_sequence(regclass, regclass, name, text, text), "
               "kernel.annotate_function(regprocedure, text, regclass), kernel.allow_vetted_delete(regclass), kernel.mark_person(regclass)")
    op.execute("DELETE FROM kernel.rls_registry WHERE rel = 'platform.jobs'::regclass")
    op.execute("DROP TABLE platform.jobs")
    op.execute("DROP TABLE kernel.job_gate_grants, kernel.sequence_waivers, kernel.lock_keys")
    op.execute("ALTER TABLE kernel.function_registry DROP COLUMN person_effect, DROP COLUMN dedupe_rel")
    op.execute("ALTER TABLE kernel.rls_registry DROP COLUMN person, DROP COLUMN vetted_delete")
