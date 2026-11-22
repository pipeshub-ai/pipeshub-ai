"""kernel: the per-org row policy for the vetted writer role (a table that requires it, the checks, and _secure_rel keeping it)"""
from pathlib import Path

from alembic import op
from db.lib.run_sql import run_sql

revision = "kernel_0017"
down_revision = "kernel_0016"
branch_labels = None
depends_on = None
older_code_compatible = ("a new registry column that defaults to false, two owner-only functions and a check view; kernel._secure_rel keeps its signature and its "
                         "result and differs only for a table that asked for the per-org policy, and no earlier table has")


def upgrade():
    run_sql(__file__)


def downgrade():
    op.execute("""DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM kernel.rls_registry WHERE vetted_org_policy) THEN
    RAISE EXCEPTION 'kernel_0017 downgrade: a table still requires the per-org vetted policy; drop it (its module downgrades first) before the kernel goes back';
  END IF;
END $$""")
    # kernel_0016's allow_vetted_write_op (this revision's calls the privilege sync that goes with the view)
    sql = (Path(__file__).parent / "0016_vetted_write_pins.sql").read_text(encoding="utf-8")
    body = sql[sql.index("CREATE FUNCTION kernel.allow_vetted_write_op"):sql.index("REVOKE ALL ON FUNCTION kernel.allow_vetted_write_op")]
    op.execute(body.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1))
    op.execute("DROP VIEW kernel.rls_violations")
    op.execute("""CREATE VIEW kernel.rls_violations WITH (security_invoker = true) AS
SELECT c.rule, c.object FROM kernel.rls_violations_core c
  WHERE NOT (c.rule = '42 vetted role pipeshub_vetted_kernel holds SELECT outside its schema' AND c.object IN (SELECT rel::text FROM kernel.vetted_extension_reads))
    AND NOT EXISTS (SELECT 1 FROM kernel.job_gate_grants j WHERE c.rule = '42 vetted role ' || j.owner || ' holds SELECT outside its schema' AND c.object = j.rel)
UNION ALL
SELECT v.rule, v.object FROM kernel.vetted_violations v
  WHERE NOT EXISTS (SELECT 1 FROM kernel.job_gate_grants j WHERE v.rule = '73 vetted role ' || j.owner || ' holds SELECT on a table that did not opt in' AND v.object = j.rel)
    AND NOT (v.rule = '57 write function uses ON CONFLICT' AND v.object IN (SELECT proc::text FROM kernel.function_registry WHERE dedupe_rel IS NOT NULL))
    AND NOT (v.rule = '57 write function uses ON CONFLICT' AND v.object IN (SELECT proc::text FROM kernel.vetted_write_pins WHERE op = 'upsert'))
UNION ALL
SELECT pv.rule, pv.object FROM kernel.person_violations pv
  WHERE NOT (pv.rule = '57 write function uses ON CONFLICT' AND pv.object IN (SELECT proc::text FROM kernel.function_registry WHERE dedupe_rel IS NOT NULL))
    AND NOT (pv.rule = '57 write function uses ON CONFLICT' AND pv.object IN (SELECT proc::text FROM kernel.vetted_write_pins WHERE op = 'upsert'))
    AND NOT (pv.rule = '88 read or write function deletes' AND pv.object IN (SELECT proc::text FROM kernel.vetted_write_pins WHERE op = 'delete'))
    AND NOT (pv.rule LIKE '89 vetted role % holds DELETE on a table without vetted_delete' AND pv.object IN (SELECT rel::text FROM kernel.vetted_write_pins WHERE op = 'delete'))
UNION ALL
SELECT rule, object FROM kernel.pin_violations""")
    op.execute("REVOKE ALL ON kernel.rls_violations FROM PUBLIC")
    op.execute("DROP VIEW kernel.org_policy_violations")
    op.execute("DROP TABLE kernel.vetted_open_ops")
    op.execute("DROP FUNCTION kernel.require_vetted_org_policy(regclass, text[]), kernel._sync_vetted_privs(regclass), kernel._vetted_org_policy(regclass, regclass)")
    op.execute("ALTER TABLE kernel.rls_registry DROP CONSTRAINT rls_registry_vetted_org_policy_shape")
    op.execute("ALTER TABLE kernel.rls_registry DROP COLUMN vetted_org_policy")
    # _secure_rel keeps its new body on a downgrade: with no table requiring the policy it behaves as kernel_0003's (the registry column is gone, so restore it)
    op.execute("""CREATE OR REPLACE FUNCTION kernel._secure_rel(p_part regclass, p_root regclass, p_class text, p_jobs boolean, p_vetted boolean,
                                              p_vetted_write boolean DEFAULT false)
RETURNS void LANGUAGE plpgsql SET search_path = pg_catalog, kernel, pg_temp AS $$
DECLARE s kernel.settings; v_vetted text; v_grantee text; is_root boolean := p_part = p_root;
BEGIN
  SELECT * INTO s FROM kernel.settings;
  v_vetted := s.vetted_prefix || (SELECT n.nspname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.oid = p_root);
  EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY, FORCE ROW LEVEL SECURITY', p_part);
  EXECUTE format('DROP POLICY IF EXISTS org_scope ON %s', p_part);
  EXECUTE format($p$CREATE POLICY org_scope ON %s TO %I
    USING (org_id = NULLIF(current_setting('app.org_id', true), ''))
    WITH CHECK (org_id = NULLIF(current_setting('app.org_id', true), ''))$p$, p_part, s.app_group);
  EXECUTE format('DROP POLICY IF EXISTS lineage_read ON %s', p_part);
  IF p_class = 'lineage' THEN
    EXECUTE format($p$CREATE POLICY lineage_read ON %s FOR SELECT TO %I
      USING (org_id = ANY (string_to_array(NULLIF(current_setting('app.org_lineage', true), ''), ',')))$p$, p_part, s.app_group);
  END IF;
  EXECUTE format('DROP POLICY IF EXISTS jobs_all ON %s', p_part);
  IF p_jobs THEN EXECUTE format('CREATE POLICY jobs_all ON %s TO %I USING (true) WITH CHECK (true)', p_part, s.jobs); END IF;
  EXECUTE format('DROP POLICY IF EXISTS vetted_read ON %s', p_part);
  IF p_vetted THEN
    EXECUTE format('CREATE POLICY vetted_read ON %s FOR SELECT TO %I USING (true)', p_part, v_vetted);
    IF is_root THEN EXECUTE format('GRANT SELECT ON %s TO %I', p_part, v_vetted); END IF;
  END IF;
  EXECUTE format('DROP POLICY IF EXISTS vetted_write ON %s', p_part);
  IF p_vetted_write THEN
    EXECUTE format('CREATE POLICY vetted_write ON %s TO %I USING (true) WITH CHECK (true)', p_part, v_vetted);
    IF is_root THEN EXECUTE format('GRANT SELECT, INSERT, UPDATE ON %s TO %I', p_part, v_vetted); END IF;
  END IF;
  EXECUTE format('DROP POLICY IF EXISTS backup_read ON %s', p_part);
  EXECUTE format('CREATE POLICY backup_read ON %s FOR SELECT TO %I USING (true)', p_part, s.backup);
  EXECUTE format('DROP POLICY IF EXISTS restore_insert ON %s', p_part);
  EXECUTE format('CREATE POLICY restore_insert ON %s FOR INSERT TO %I WITH CHECK (kernel.restore_allows(org_id))', p_part, s.restore);
  IF NOT is_root THEN
    EXECUTE format('REVOKE ALL ON %s FROM PUBLIC', p_part);
    FOR v_grantee IN SELECT DISTINCT a.grantee::regrole::text FROM pg_class c, aclexplode(c.relacl) a
                     WHERE c.oid = p_part AND a.grantee <> 0 AND a.grantee <> c.relowner LOOP
      EXECUTE format('REVOKE ALL ON %s FROM %s', p_part, v_grantee);
    END LOOP;
  END IF;
  EXECUTE format('GRANT SELECT ON %s TO %I', p_part, s.backup);
  EXECUTE format('GRANT INSERT ON %s TO %I', p_part, s.restore);
  EXECUTE format('GRANT USAGE ON SCHEMA %I TO %I, %I', (SELECT n.nspname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                                                         WHERE c.oid = p_part), s.backup, s.restore);
  IF is_root THEN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = p_part AND conname = 'org_id_not_empty') THEN
      EXECUTE format($c$ALTER TABLE %s ADD CONSTRAINT org_id_not_empty CHECK (org_id <> '')$c$, p_part);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = p_part AND conname = 'org_id_format') THEN
      EXECUTE format($c$ALTER TABLE %s ADD CONSTRAINT org_id_format CHECK (org_id ~ %L)$c$, p_part, s.org_id_pattern);
    END IF;
  END IF;
END $$""")
