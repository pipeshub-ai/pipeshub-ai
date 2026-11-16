-- Row policies on an internal table. An internal table has no org column, but its rows may still be split between the service roles
-- (the key-value table by key prefix, say). Check 04 refuses every policy the org templates do not name; a policy registered here with
-- kernel.register_internal_policy is exempt from it, and checks 140 and 141 keep it exactly as it was registered (command, permissive,
-- roles and both expressions), so an edited, dropped or extra policy is still a violation.
CREATE TABLE kernel.internal_policies (
  rel     regclass NOT NULL,
  polname name NOT NULL,
  def_md5 text NOT NULL,
  PRIMARY KEY (rel, polname));
REVOKE ALL ON kernel.internal_policies FROM PUBLIC;

-- deparsed with a fixed path, so the registration and the check agree whatever the caller's search_path; a JSON array keeps every
-- field in its place, NULLs included, so the same expression moved from USING to WITH CHECK is a different fingerprint
CREATE FUNCTION kernel._internal_policy_md5(p_rel regclass, p_polname name) RETURNS text
LANGUAGE sql STABLE SET search_path = pg_catalog AS $$
  SELECT md5(jsonb_build_array(p.polcmd::text, p.polpermissive,
                               (SELECT jsonb_agg(r.rolname ORDER BY r.rolname) FROM pg_roles r WHERE r.oid = ANY (p.polroles)),
                               p.polroles = '{0}'::oid[],
                               pg_get_expr(p.polqual, p.polrelid), pg_get_expr(p.polwithcheck, p.polrelid))::text)
  FROM pg_policy p WHERE p.polrelid = p_rel AND p.polname = p_polname $$;

CREATE FUNCTION kernel.register_internal_policy(p_rel regclass, p_polname name) RETURNS void
LANGUAGE plpgsql SET search_path = pg_catalog, kernel, pg_temp AS $$
DECLARE v_md5 text;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM kernel.rls_registry WHERE rel = p_rel AND class = 'internal') THEN
    RAISE EXCEPTION 'register_internal_policy: % is not a registered internal table', p_rel;
  END IF;
  v_md5 := kernel._internal_policy_md5(p_rel, p_polname);
  IF v_md5 IS NULL THEN
    RAISE EXCEPTION 'register_internal_policy: % has no policy %', p_rel, p_polname;
  END IF;
  INSERT INTO kernel.internal_policies (rel, polname, def_md5) VALUES (p_rel, p_polname, v_md5)
    ON CONFLICT (rel, polname) DO UPDATE SET def_md5 = excluded.def_md5;
END $$;
REVOKE ALL ON FUNCTION kernel._internal_policy_md5(regclass, name), kernel.register_internal_policy(regclass, name) FROM PUBLIC;

-- kernel_0017's view, with check 04 leaving out the registered internal policies, plus checks 140 and 141
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
  WHERE kernel._internal_policy_md5(ip.rel, ip.polname) <> ip.def_md5;
REVOKE ALL ON kernel.rls_violations FROM PUBLIC;
