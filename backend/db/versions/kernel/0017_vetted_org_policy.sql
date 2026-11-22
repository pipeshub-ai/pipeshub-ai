-- A per-org row policy for the vetted writer role. The vetted role's policy was `USING (true)`: the org binding of a vetted write
-- function was its own text (and the checks and tests that read it). A table registered here (`kernel.require_vetted_org_policy`) gives the role
-- the same org predicate the application roles have, so the database refuses a cross-org write or read even through a function with a wrong
-- body. Such a table is an `org` table with `vetted_write`, no `vetted_read` (that policy is unbound), and every pinned table must be one (rule 118).

ALTER TABLE kernel.rls_registry ADD COLUMN vetted_org_policy boolean NOT NULL DEFAULT false;
ALTER TABLE kernel.rls_registry ADD CONSTRAINT rls_registry_vetted_org_policy_shape
  CHECK (NOT vetted_org_policy OR (class = 'org' AND vetted_write AND NOT vetted_read)) NOT VALID;
ALTER TABLE kernel.rls_registry VALIDATE CONSTRAINT rls_registry_vetted_org_policy_shape;

-- ONE relation (no recursion): the vetted role's policy is the org predicate; the role is that of the schema of the root p_root. Used by require_vetted_org_policy and by _secure_rel.
CREATE FUNCTION kernel._vetted_org_policy(p_part regclass, p_root regclass) RETURNS void
LANGUAGE plpgsql SET search_path = pg_catalog, kernel, pg_temp AS $fn$
DECLARE s kernel.settings; v name;
BEGIN
  SELECT * INTO s FROM kernel.settings;
  SELECT s.vetted_prefix || n.nspname INTO v FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.oid = p_root;   -- the root's schema, as check 119 and apply_rls read it
  EXECUTE format('DROP POLICY IF EXISTS vetted_write ON %s', p_part);
  EXECUTE format($p$CREATE POLICY vetted_write ON %s TO %I
    USING (org_id = NULLIF(current_setting('app.org_id', true), ''))
    WITH CHECK (org_id = NULLIF(current_setting('app.org_id', true), ''))$p$, p_part, v);
END $fn$;

-- Operations on an access table that application roles keep (the capture trigger inserts into the change log as the role that wrote the graph).
CREATE TABLE kernel.vetted_open_ops (rel regclass NOT NULL, op text NOT NULL CHECK (op IN ('insert', 'update', 'delete')), PRIMARY KEY (rel, op));
REVOKE ALL ON TABLE kernel.vetted_open_ops FROM PUBLIC;

-- The privileges the vetted role of a table's schema holds on it are exactly what its pins need (an upsert-only table has no DELETE, a table nobody pinned
-- no write at all): applied to the root only, partitions hold nothing (check 10).
CREATE FUNCTION kernel._sync_vetted_privs(p_rel regclass) RETURNS void
LANGUAGE plpgsql SET search_path = pg_catalog, kernel, pg_temp AS $fn$
DECLARE s kernel.settings; v name; ins boolean; upd boolean; del boolean;
BEGIN
  SELECT * INTO s FROM kernel.settings;
  SELECT s.vetted_prefix || n.nspname INTO v FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.oid = p_rel;
  SELECT COALESCE(bool_or(op IN ('insert', 'upsert')), false), COALESCE(bool_or(op IN ('update', 'upsert')), false), COALESCE(bool_or(op = 'delete'), false)
    INTO ins, upd, del FROM kernel.vetted_write_pins WHERE rel = p_rel;
  EXECUTE format('%s INSERT ON %s %s %I', CASE WHEN ins THEN 'GRANT' ELSE 'REVOKE' END, p_rel, CASE WHEN ins THEN 'TO' ELSE 'FROM' END, v);
  EXECUTE format('%s UPDATE ON %s %s %I', CASE WHEN upd THEN 'GRANT' ELSE 'REVOKE' END, p_rel, CASE WHEN upd THEN 'TO' ELSE 'FROM' END, v);
  EXECUTE format('%s DELETE ON %s %s %I', CASE WHEN del THEN 'GRANT' ELSE 'REVOKE' END, p_rel, CASE WHEN del THEN 'TO' ELSE 'FROM' END, v);
END $fn$;

CREATE FUNCTION kernel.require_vetted_org_policy(p_rel regclass, p_open_ops text[] DEFAULT '{}') RETURNS int
LANGUAGE plpgsql SET search_path = pg_catalog, kernel, pg_temp AS $fn$
DECLARE r kernel.rls_registry; part regclass; n int := 0; o text;
BEGIN
  SELECT * INTO r FROM kernel.rls_registry WHERE rel = p_rel;
  IF r IS NULL OR r.class <> 'org' OR NOT r.vetted_write OR r.vetted_read THEN
    RAISE EXCEPTION 'require_vetted_org_policy: % is not a registered org table with vetted_write and without vetted_read (kernel.apply_rls first)', p_rel;
  END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_open_ops) x WHERE x NOT IN ('insert', 'update', 'delete')) THEN
    RAISE EXCEPTION 'require_vetted_org_policy: the open operations are insert, update or delete';
  END IF;
  UPDATE kernel.rls_registry SET vetted_org_policy = true WHERE rel = p_rel;
  DELETE FROM kernel.vetted_open_ops WHERE rel = p_rel;
  FOREACH o IN ARRAY p_open_ops LOOP INSERT INTO kernel.vetted_open_ops VALUES (p_rel, o); END LOOP;
  FOR part IN SELECT p_rel UNION SELECT relid FROM pg_partition_tree(p_rel) LOOP
    PERFORM kernel._vetted_org_policy(part, p_rel);
    n := n + 1;
  END LOOP;
  PERFORM kernel._sync_vetted_privs(p_rel);
  RETURN n;
END $fn$;

CREATE OR REPLACE FUNCTION kernel.allow_vetted_write_op(p_proc regprocedure, p_rel regclass, p_op text, p_key name[] DEFAULT NULL,
                                             p_set name[] DEFAULT NULL, p_txn name DEFAULT NULL, p_max_items int DEFAULT 5000) RETURNS void
LANGUAGE plpgsql SET search_path = pg_catalog, kernel, pg_temp AS $fn$
DECLARE s kernel.settings; g kernel.function_registry; r kernel.rls_registry; v_nsp name; v_vetted name; v_pk name[];
BEGIN
  SELECT * INTO s FROM kernel.settings;
  SELECT * INTO g FROM kernel.function_registry WHERE proc = p_proc;
  IF g IS NULL OR g.class <> 'write' OR g.scope <> 'org' THEN
    RAISE EXCEPTION 'allow_vetted_write_op: % is not a registered org-scoped write function', p_proc;
  END IF;
  SELECT * INTO r FROM kernel.rls_registry WHERE rel = p_rel;
  IF r IS NULL OR r.class <> 'org' OR NOT r.vetted_write OR EXISTS (SELECT 1 FROM pg_class c WHERE c.oid = p_rel AND c.relispartition) THEN
    RAISE EXCEPTION 'allow_vetted_write_op: % is not the root of a registered org table with vetted_write (kernel.apply_rls first)', p_rel;
  END IF;
  SELECT n.nspname INTO v_nsp FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.oid = p_rel;
  v_vetted := s.vetted_prefix || v_nsp;
  IF NOT EXISTS (SELECT 1 FROM pg_proc x WHERE x.oid = p_proc::oid AND x.prosecdef AND x.proowner = to_regrole(v_vetted)) THEN
    RAISE EXCEPTION 'allow_vetted_write_op: % is not a SECURITY DEFINER function owned by %', p_proc, v_vetted;
  END IF;
  IF p_op NOT IN ('insert', 'upsert', 'update', 'delete') THEN RAISE EXCEPTION 'allow_vetted_write_op: unknown operation %', p_op; END IF;
  SELECT array_agg(a.attname ORDER BY k.ord) INTO v_pk
    FROM pg_index i CROSS JOIN LATERAL unnest(i.indkey::int2[]) WITH ORDINALITY k(attnum, ord)
    JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = k.attnum
    WHERE i.indrelid = p_rel AND i.indisprimary;
  IF v_pk IS NULL OR v_pk[1] <> 'org_id' THEN
    RAISE EXCEPTION 'allow_vetted_write_op: % needs a primary key led by org_id', p_rel;
  END IF;
  IF p_max_items NOT BETWEEN 1 AND 5000 THEN RAISE EXCEPTION 'allow_vetted_write_op: max_items is 1..5000'; END IF;

  IF p_op = 'delete' THEN
    IF (p_key IS NULL) = (p_txn IS NULL) OR p_set IS NOT NULL THEN
      RAISE EXCEPTION 'allow_vetted_write_op: a delete is pinned by its key columns or by its transaction column, one of them';
    END IF;
    IF p_key IS NOT NULL AND NOT (cardinality(p_key) = cardinality(v_pk) - 1 AND p_key @> v_pk[2:] AND p_key <@ v_pk[2:]) THEN
      RAISE EXCEPTION 'allow_vetted_write_op: a delete by key names the whole primary key after org_id (%), nothing else', v_pk[2:];
    END IF;
    IF p_txn IS NOT NULL AND NOT EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = p_rel AND a.attname = p_txn AND a.atttypid = 'xid8'::regtype AND NOT a.attisdropped) THEN
      RAISE EXCEPTION 'allow_vetted_write_op: % has no xid8 column %', p_rel, p_txn;
    END IF;
    EXECUTE format('GRANT DELETE ON %s TO %I', p_rel, v_vetted);
  ELSIF p_op = 'upsert' THEN
    IF p_set IS NULL OR p_key IS NOT NULL OR p_txn IS NOT NULL THEN
      RAISE EXCEPTION 'allow_vetted_write_op: an upsert is pinned by its DO UPDATE SET columns (an empty list is DO NOTHING)';
    END IF;
    IF p_set && v_pk OR EXISTS (SELECT 1 FROM unnest(p_set) c WHERE NOT EXISTS (
         SELECT 1 FROM pg_attribute a WHERE a.attrelid = p_rel AND a.attname = c AND a.attnum > 0 AND NOT a.attisdropped AND a.attgenerated = '')) THEN
      RAISE EXCEPTION 'allow_vetted_write_op: an upsert sets columns of % that are not part of its primary key', p_rel;
    END IF;
  ELSIF p_key IS NOT NULL OR p_set IS NOT NULL OR p_txn IS NOT NULL THEN
    RAISE EXCEPTION 'allow_vetted_write_op: an % has no key, set or transaction column', p_op;
  END IF;

  INSERT INTO kernel.vetted_write_pins (proc, rel, op, key_cols, set_cols, txn_col, max_items) VALUES (p_proc, p_rel, p_op, p_key, p_set, p_txn, p_max_items)
    ON CONFLICT (proc, rel, op) DO UPDATE SET key_cols = excluded.key_cols, set_cols = excluded.set_cols, txn_col = excluded.txn_col, max_items = excluded.max_items;
  IF r.vetted_org_policy THEN PERFORM kernel._sync_vetted_privs(p_rel); END IF;
END $fn$;

-- _secure_rel as it was (kernel_0003), with the vetted_write policy of a registered table being the org predicate. New leaves
-- (apply_rls_partition) and a repeated apply_rls therefore keep it.
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
REVOKE ALL ON FUNCTION kernel._vetted_org_policy(regclass, regclass), kernel._sync_vetted_privs(regclass), kernel.require_vetted_org_policy(regclass, text[]) FROM PUBLIC;
REVOKE ALL ON FUNCTION kernel.allow_vetted_write_op(regprocedure, regclass, text, name[], name[], name, int) FROM PUBLIC;

-- The checks. Zero rows = pass.
CREATE VIEW kernel.org_policy_violations WITH (security_invoker = true) AS
WITH RECURSIVE s AS (SELECT * FROM kernel.settings),
trusted AS (SELECT r.oid FROM pg_roles r, s WHERE r.rolname IN (s.owner, s.migrate, s.backup, s.restore) OR r.rolname LIKE s.vetted_prefix || '%'),
admin_roles AS (SELECT r.oid FROM pg_roles r, s WHERE r.rolname = ANY (s.admins)),
rall AS (SELECT r.oid FROM pg_roles r WHERE NOT r.rolsuper AND r.rolname NOT LIKE 'pg\_%'),
actors AS (SELECT oid FROM rall WHERE oid NOT IN (SELECT oid FROM trusted) AND oid NOT IN (SELECT oid FROM admin_roles)),
reach AS (
  WITH RECURSIVE t(actor, role) AS (
    SELECT a.oid, a.oid FROM rall a UNION SELECT t.actor, m.roleid FROM t JOIN pg_auth_members m ON m.member = t.role)
  SELECT * FROM t),
reach_roles AS (SELECT DISTINCT role AS oid FROM reach WHERE actor IN (SELECT oid FROM actors)),
flagged AS (SELECT r.rel AS root FROM kernel.rls_registry r WHERE r.vetted_org_policy),
tree AS (SELECT f.root, f.root AS oid FROM flagged f UNION SELECT f.root, t.relid FROM flagged f, LATERAL pg_partition_tree(f.root) t),
pol AS (
  SELECT p.polrelid AS oid, p.polname AS name, p.polcmd AS cmd, p.polpermissive AS permissive,
         pg_get_expr(p.polqual, p.polrelid) AS qual, pg_get_expr(p.polwithcheck, p.polrelid) AS with_check,
         (SELECT array_agg(r.rolname ORDER BY r.rolname) FROM pg_roles r WHERE r.oid = ANY (p.polroles)) AS roles,
         (p.polroles = '{0}'::oid[]) AS public_role
  FROM pg_policy p),
tmpl AS (SELECT qual, with_check FROM kernel.policy_template WHERE name = 'org_scope'),
cat AS (
SELECT '118 pinned table has no per-org policy for the vetted writer role' AS rule, p.rel::text AS object
  FROM (SELECT DISTINCT rel FROM kernel.vetted_write_pins) p WHERE NOT EXISTS (SELECT 1 FROM flagged f WHERE f.root = p.rel)
UNION ALL -- 128 a table of a schema that owns pinned relations, written by the schema's vetted role and not read unbound, must require the policy too, pinned or not (a new access table that forgets the call)
SELECT '128 table of a schema that owns pinned relations does not require the per-org policy', r.rel::text
  FROM kernel.rls_registry r JOIN pg_class c ON c.oid = r.rel
  WHERE r.class = 'org' AND r.vetted_write AND NOT r.vetted_read AND NOT r.vetted_org_policy
    AND EXISTS (SELECT 1 FROM kernel.vetted_write_pins p JOIN pg_class pc ON pc.oid = p.rel WHERE pc.relnamespace = c.relnamespace)
UNION ALL
SELECT '119 vetted writer policy is not the per-org predicate on a table that requires it', t.oid::regclass::text FROM tree t, s
  WHERE NOT EXISTS (SELECT 1 FROM pol, tmpl WHERE pol.oid = t.oid AND pol.name = 'vetted_write' AND pol.cmd = '*' AND pol.permissive
                    AND pol.qual = tmpl.qual AND pol.with_check = tmpl.with_check
                    AND pol.roles = ARRAY[s.vetted_prefix || (SELECT n.nspname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.oid = t.root)]::name[])
UNION ALL -- 124 the vetted role holds exactly the privileges its pins need on a table that requires the policy (an upsert-only table has no DELETE; one nobody pinned, no write)
SELECT '124 vetted role ' || g.grantee::regrole::text || ' holds ' || g.privilege_type || ' on ' || g.oid::regclass::text || ', which no pin needs', g.oid::regclass::text
  FROM (SELECT f.root, c.oid, a.grantee, a.privilege_type FROM flagged f JOIN pg_class c ON c.oid = f.root, LATERAL aclexplode(c.relacl) a) g, s
  WHERE g.grantee = to_regrole(s.vetted_prefix || (SELECT n.nspname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.oid = g.root))::oid
    AND g.privilege_type IN ('INSERT', 'UPDATE', 'DELETE', 'TRUNCATE')
    AND NOT EXISTS (SELECT 1 FROM kernel.vetted_write_pins p WHERE p.rel = g.root
                    AND ((g.privilege_type = 'INSERT' AND p.op IN ('insert', 'upsert')) OR (g.privilege_type = 'UPDATE' AND p.op IN ('update', 'upsert')) OR (g.privilege_type = 'DELETE' AND p.op = 'delete')))
UNION ALL -- 126 no application role writes a table that requires the policy, except the operations the migration left open (the change log's INSERT): pins or no pins, every table of this kind is closed
SELECT '126 ' || CASE WHEN a.grantee = 0 THEN 'PUBLIC' ELSE a.grantee::regrole::text END || ' holds ' || a.privilege_type || ' on a table that only its vetted functions may write', t.oid::regclass::text
  FROM tree t JOIN pg_class c ON c.oid = t.oid, LATERAL aclexplode(c.relacl) a
  WHERE a.privilege_type IN ('INSERT', 'UPDATE', 'DELETE', 'TRUNCATE') AND (a.grantee = 0 OR a.grantee IN (SELECT oid FROM reach_roles))
    AND NOT EXISTS (SELECT 1 FROM kernel.vetted_open_ops o WHERE o.rel = t.root AND upper(o.op) = a.privilege_type)
UNION ALL -- 127 the same for a column privilege
SELECT '127 ' || CASE WHEN a.grantee = 0 THEN 'PUBLIC' ELSE a.grantee::regrole::text END || ' holds ' || a.privilege_type || ' on column ' || at.attname || ' of a table that only its vetted functions may write', t.oid::regclass::text
  FROM tree t JOIN pg_attribute at ON at.attrelid = t.oid AND at.attacl IS NOT NULL AND NOT at.attisdropped, LATERAL aclexplode(at.attacl) a
  WHERE a.privilege_type IN ('INSERT', 'UPDATE') AND (a.grantee = 0 OR a.grantee IN (SELECT oid FROM reach_roles))
    AND NOT EXISTS (SELECT 1 FROM kernel.vetted_open_ops o WHERE o.rel = t.root AND upper(o.op) = a.privilege_type)
UNION ALL
SELECT '119 vetted role has another policy on a table that requires the per-org predicate: ' || pol.name, t.oid::regclass::text FROM tree t JOIN pol ON pol.oid = t.oid, s
  WHERE pol.name <> 'vetted_write' AND (pol.public_role OR EXISTS (SELECT 1 FROM unnest(pol.roles) r WHERE r LIKE s.vetted_prefix || '%'))
)
SELECT rule, object FROM cat;
REVOKE ALL ON kernel.org_policy_violations FROM PUBLIC;

-- The public view: kernel_0015's, with rule 05 for the vetted_write policy of a table that requires the per-org predicate left to the checks
-- above, plus those checks.
DROP VIEW kernel.rls_violations;
CREATE VIEW kernel.rls_violations WITH (security_invoker = true) AS
SELECT c.rule, c.object FROM kernel.rls_violations_core c
  WHERE NOT (c.rule = '42 vetted role pipeshub_vetted_kernel holds SELECT outside its schema' AND c.object IN (SELECT rel::text FROM kernel.vetted_extension_reads))
    AND NOT EXISTS (SELECT 1 FROM kernel.job_gate_grants j WHERE c.rule = '42 vetted role ' || j.owner || ' holds SELECT outside its schema' AND c.object = j.rel)
    AND NOT (c.rule = '05 policy differs from template vetted_write' AND c.object IN (
      SELECT x.oid::regclass::text FROM (SELECT r.rel AS oid FROM kernel.rls_registry r WHERE r.vetted_org_policy
                                         UNION SELECT t.relid FROM kernel.rls_registry r, LATERAL pg_partition_tree(r.rel) t WHERE r.vetted_org_policy) x))
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
SELECT rule, object FROM kernel.org_policy_violations;
REVOKE ALL ON kernel.rls_violations FROM PUBLIC;
