-- An org's rows can be removed by the owner role under row security, with no change to the table (no lifted FORCE, no disabled
-- trigger, no lock beyond the rows): every org table and partition gets the policy org_erase, for the owner, that admits the rows
-- of an org kernel.org_erase_allows names. Here it names none, so the owner still reaches no org row. An extra migration location
-- may replace the body (the security surface pins it). WITH CHECK is the same test, so the triggers that write derived rows of that
-- org while its rows go (change logs, counters) can do so, and nothing of another org can be written.
CREATE FUNCTION kernel.org_erase_allows(p_org text) RETURNS boolean
LANGUAGE sql STABLE SET search_path = pg_catalog, kernel, pg_temp AS $$ SELECT false $$;
REVOKE ALL ON FUNCTION kernel.org_erase_allows(text) FROM PUBLIC;

-- Whether a relation carries org_erase exactly: permissive, every command, the owner role only, both expressions the call above.
-- (kernel is not on its search_path, so pg_get_expr spells the function with its schema, as the policy text has it)
CREATE FUNCTION kernel._org_erase_policy_ok(p_rel regclass) RETURNS boolean
LANGUAGE sql STABLE SET search_path = pg_catalog, pg_temp AS $$
  SELECT EXISTS (SELECT 1 FROM pg_policy p, kernel.settings s WHERE p.polrelid = p_rel AND p.polname = 'org_erase' AND p.polpermissive
                 AND p.polcmd = '*' AND p.polroles = ARRAY[to_regrole(s.owner)::oid]
                 AND pg_get_expr(p.polqual, p.polrelid) = 'kernel.org_erase_allows(org_id)'
                 AND pg_get_expr(p.polwithcheck, p.polrelid) = 'kernel.org_erase_allows(org_id)') $$;
REVOKE ALL ON FUNCTION kernel._org_erase_policy_ok(regclass) FROM PUBLIC;

-- kernel_0017's _secure_rel, which also gives each relation org_erase: new tables and new leaves get it with the others.
CREATE OR REPLACE FUNCTION kernel._secure_rel(p_part regclass, p_root regclass, p_class text, p_jobs boolean, p_vetted boolean,
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
    IF is_root THEN EXECUTE format('GRANT SELECT ON %s TO %I', p_part, v_vetted); END IF;  -- functions read through the parent
  END IF;
  EXECUTE format('DROP POLICY IF EXISTS vetted_write ON %s', p_part);
  IF p_vetted_write THEN
    IF EXISTS (SELECT 1 FROM kernel.rls_registry WHERE rel = p_root AND vetted_org_policy) THEN
      PERFORM kernel._vetted_org_policy(p_part, p_root);
    ELSE
      EXECUTE format('CREATE POLICY vetted_write ON %s TO %I USING (true) WITH CHECK (true)', p_part, v_vetted);
    END IF;
    IF is_root THEN
      EXECUTE format('GRANT SELECT ON %s TO %I', p_part, v_vetted);
      IF EXISTS (SELECT 1 FROM kernel.rls_registry WHERE rel = p_root AND vetted_org_policy) THEN PERFORM kernel._sync_vetted_privs(p_root);
      ELSE EXECUTE format('GRANT INSERT, UPDATE ON %s TO %I', p_part, v_vetted); END IF;
    END IF;
  END IF;
  EXECUTE format('DROP POLICY IF EXISTS org_erase ON %s', p_part);
  EXECUTE format('CREATE POLICY org_erase ON %s TO %I USING (kernel.org_erase_allows(org_id)) WITH CHECK (kernel.org_erase_allows(org_id))', p_part, s.owner);
  -- backup reads every org (SELECT only); restore inserts one org's rows inside a window (INSERT only).
  -- Neither can alter, delete or own anything, and neither bypasses RLS.
  EXECUTE format('DROP POLICY IF EXISTS backup_read ON %s', p_part);
  EXECUTE format('CREATE POLICY backup_read ON %s FOR SELECT TO %I USING (true)', p_part, s.backup);
  EXECUTE format('DROP POLICY IF EXISTS restore_insert ON %s', p_part);
  EXECUTE format('CREATE POLICY restore_insert ON %s FOR INSERT TO %I WITH CHECK (kernel.restore_allows(org_id))', p_part, s.restore);
  IF NOT is_root THEN  -- partitions: only the owner (plus the backup SELECT / restore INSERT that dumps address) holds privileges
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
END $$;

-- Every org table and partition secured again with it (what a new leaf gets; idempotent).
SELECT count(*) FROM (SELECT kernel.apply_rls_partition(t.relid, g.rel) FROM kernel.rls_registry g,
  LATERAL (SELECT g.rel AS relid UNION SELECT relid FROM pg_partition_tree(g.rel)) t WHERE g.class IN ('org', 'lineage')) x;

-- kernel_0019's view, with check 04 accepting org_erase where it has exactly its shape, and check 142 requiring it everywhere.
DROP VIEW kernel.rls_violations;
CREATE VIEW kernel.rls_violations WITH (security_invoker = true) AS
SELECT c.rule, c.object FROM kernel.rls_violations_core c
  WHERE NOT (c.rule = '42 vetted role pipeshub_vetted_kernel holds SELECT outside its schema' AND c.object IN (SELECT rel::text FROM kernel.vetted_extension_reads))
    AND NOT EXISTS (SELECT 1 FROM kernel.job_gate_grants j WHERE c.rule = '42 vetted role ' || j.owner || ' holds SELECT outside its schema' AND c.object = j.rel)
    AND NOT (c.rule = '05 policy differs from template vetted_write' AND c.object IN (
      SELECT x.oid::regclass::text FROM (SELECT r.rel AS oid FROM kernel.rls_registry r WHERE r.vetted_org_policy
                                         UNION SELECT t.relid FROM kernel.rls_registry r, LATERAL pg_partition_tree(r.rel) t WHERE r.vetted_org_policy) x))
    AND NOT EXISTS (SELECT 1 FROM kernel.internal_policies ip JOIN kernel.rls_registry g ON g.rel = ip.rel AND g.class = 'internal'
                    WHERE c.rule = '04 unexpected policy ' || ip.polname AND c.object = ip.rel::text)
    AND NOT (c.rule = '04 unexpected policy org_erase' AND kernel._org_erase_policy_ok(c.object::regclass))
    AND NOT (c.rule = '37 unexpected membership ' || (SELECT role_prefix FROM kernel.settings) || 'monitor -> pg_monitor'
             AND NOT EXISTS (SELECT 1 FROM pg_auth_members m WHERE m.member = to_regrole((SELECT role_prefix FROM kernel.settings) || 'monitor')
                             AND m.roleid = to_regrole('pg_monitor') AND (m.admin_option OR m.set_option OR NOT m.inherit_option)))
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
SELECT rule, object FROM kernel.pin_violations
UNION ALL
SELECT rule, object FROM kernel.org_policy_violations
UNION ALL -- 140 a registered internal policy is gone, or its table is no longer internal
SELECT '140 registered internal policy is missing: ' || ip.polname, ip.rel::text FROM kernel.internal_policies ip
  WHERE kernel._internal_policy_md5(ip.rel, ip.polname) IS NULL
     OR NOT EXISTS (SELECT 1 FROM kernel.rls_registry g WHERE g.rel = ip.rel AND g.class = 'internal')
UNION ALL -- 141 edited command, roles, expression or a RESTRICTIVE/permissive flip
SELECT '141 internal policy differs from its registration: ' || ip.polname, ip.rel::text FROM kernel.internal_policies ip
  WHERE kernel._internal_policy_md5(ip.rel, ip.polname) <> ip.def_md5
UNION ALL -- 142 an org table or partition without the owner's org_erase policy, or with another one
SELECT '142 org_erase policy is missing or differs', x.rel::text FROM (
  SELECT g.rel FROM kernel.rls_registry g WHERE g.class IN ('org', 'lineage')
  UNION SELECT t.relid FROM kernel.rls_registry g, LATERAL pg_partition_tree(g.rel) t WHERE g.class IN ('org', 'lineage')) x
  WHERE NOT kernel._org_erase_policy_ok(x.rel);
REVOKE ALL ON kernel.rls_violations FROM PUBLIC;
