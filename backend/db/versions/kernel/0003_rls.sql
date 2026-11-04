-- Org isolation: how a table gets row-level security, and the registry the catalog check compares against.
-- Roles the helpers name in policies. Normally created by the deployment bootstrap (python -m db.bootstrap_roles);
-- created here, NOLOGIN, only when missing (a bare database, or tests). The migrate role has no CREATEROLE.
RESET ROLE;
DO $$
DECLARE r text;
BEGIN
  FOREACH r IN ARRAY ARRAY['pipeshub_jobs', 'pipeshub_ro', 'pipeshub_backup', 'pipeshub_restore'] LOOP
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
      EXECUTE format('CREATE ROLE %I NOLOGIN', r);
    END IF;
  END LOOP;
  IF NOT EXISTS (SELECT 1 FROM pg_auth_members m JOIN pg_roles a ON a.oid = m.roleid JOIN pg_roles b ON b.oid = m.member
                 WHERE a.rolname = 'pipeshub_app' AND b.rolname = 'pipeshub_ro') THEN
    GRANT pipeshub_app TO pipeshub_ro WITH INHERIT TRUE, SET FALSE;
  END IF;
EXCEPTION WHEN insufficient_privilege THEN
  RAISE EXCEPTION 'org isolation roles are missing and this role cannot create them: run the role bootstrap (python -m db.bootstrap_roles)';
END $$;
-- Server-side large objects have no org; the bootstrap revokes them from PUBLIC, and a superuser migration does too.
DO $$ DECLARE f regprocedure; BEGIN
  FOR f IN SELECT p.oid::regprocedure FROM pg_proc p WHERE p.pronamespace = 'pg_catalog'::regnamespace
           AND p.proname ~ '^(lo_|lo(read|write)$)' LOOP
    EXECUTE format('REVOKE EXECUTE ON FUNCTION %s FROM PUBLIC', f);
  END LOOP;
EXCEPTION WHEN insufficient_privilege THEN NULL;
END $$;
SET LOCAL ROLE pipeshub_owner;

-- Role names and rules the helpers and the catalog check use (one row).
CREATE TABLE kernel.settings (
  one            boolean PRIMARY KEY DEFAULT true CHECK (one),
  owner          name NOT NULL,
  migrate        name NOT NULL,
  app_group      name NOT NULL,
  jobs           name NOT NULL,
  role_prefix    text NOT NULL,   -- every role of ours starts with it; the catalog check treats those roles as actors
  vetted_prefix  text NOT NULL,   -- one vetted owner per schema: <prefix><schema>
  backup         name NOT NULL,   -- SELECT-only, backup_read FOR SELECT policy, no memberships, no BYPASSRLS
  restore        name NOT NULL,   -- NOLOGIN except inside a restore window; INSERT-only, scoped by kernel.restore_window
  ro             name,            -- the one LOGIN allowed to inherit the policy group (replicas, analytics)
  services       name[] NOT NULL, -- NOLOGIN service roles; each has one login that may SET into it
  admins         name[] NOT NULL DEFAULT '{}',  -- reviewed human administrators: may hold admin roles, but no application role and no application login may link to them
  extensions     name[] NOT NULL,               -- extensions that may be installed; their members are not subject to the semantic checks
  org_id_pattern text NOT NULL);  -- ids are comma-free (lineage lists are comma-joined)
INSERT INTO kernel.settings VALUES (true, 'pipeshub_owner', 'pipeshub_migrate', 'pipeshub_app', 'pipeshub_jobs',
  'pipeshub_', 'pipeshub_vetted_', 'pipeshub_backup', 'pipeshub_restore', 'pipeshub_ro',
  ARRAY['pipeshub_node', 'pipeshub_connectors', 'pipeshub_indexing', 'pipeshub_query'], '{}',
  ARRAY['plpgsql', 'vector', 'pg_trgm', 'pg_textsearch', 'btree_gin', 'pg_search', 'pg_stat_statements'], '^[0-9a-f]{24}$');

-- Every relation that holds org data, with its class. An unregistered table with an org_id column is a violation.
CREATE TABLE kernel.rls_registry (
  rel          regclass PRIMARY KEY,
  class        text NOT NULL CHECK (class IN ('org', 'lineage', 'global', 'internal')),   -- internal: no org data, plain grants
  jobs_access  boolean NOT NULL DEFAULT false,
  vetted_read  boolean NOT NULL DEFAULT false,
  vetted_write boolean NOT NULL DEFAULT false);

-- A table without org data (profile row, counters) is registered as internal: it must not have an org_id column.
CREATE FUNCTION kernel.register_internal(p_rel regclass) RETURNS void
LANGUAGE sql SET search_path = pg_catalog, kernel, pg_temp AS $$
  INSERT INTO kernel.rls_registry (rel, class) VALUES (p_rel, 'internal') ON CONFLICT (rel) DO UPDATE SET class = 'internal' $$;

-- Canonical policy expressions: the catalog check compares every policy on a registered relation with these
-- deparsed texts, so a stray or edited permissive policy is a violation.
CREATE TABLE kernel.policy_template (name text PRIMARY KEY, cmd "char" NOT NULL, qual text, with_check text);
INSERT INTO kernel.policy_template VALUES
  ('org_scope', '*', $$(org_id = NULLIF(current_setting('app.org_id'::text, true), ''::text))$$,
                  $$(org_id = NULLIF(current_setting('app.org_id'::text, true), ''::text))$$),
  ('lineage_read', 'r', $$(org_id = ANY (string_to_array(NULLIF(current_setting('app.org_lineage'::text, true), ''::text), ','::text)))$$, NULL),
  ('jobs_all', '*', 'true', 'true'),
  ('vetted_read', 'r', 'true', NULL),
  ('vetted_write', '*', 'true', 'true'),
  ('backup_read', 'r', 'true', NULL),
  ('restore_insert', 'a', NULL, 'kernel.restore_allows(org_id)');

-- Reviewed exemptions for keys that must be unique across orgs (org slug, OAuth client_id, webhook ids).
-- Written only by migrations; the constraint definition is pinned by md5.
CREATE TABLE kernel.rls_exemptions (
  rel regclass NOT NULL, conname name NOT NULL, condef_md5 text NOT NULL,
  value_source text NOT NULL CHECK (value_source IN ('sequence', 'random', 'external_issuer', 'public_by_design')),
  reason text NOT NULL, reviewed_by text NOT NULL, reviewed_at date NOT NULL DEFAULT current_date,
  PRIMARY KEY (rel, conname));

CREATE FUNCTION kernel.exempt_constraint(p_rel regclass, p_conname name, p_value_source text, p_reason text, p_reviewed_by text)
RETURNS void LANGUAGE plpgsql SET search_path = pg_catalog, kernel, pg_temp AS $$
BEGIN
  INSERT INTO kernel.rls_exemptions (rel, conname, condef_md5, value_source, reason, reviewed_by)
    SELECT p_rel, p_conname, md5(pg_get_constraintdef(c.oid)), p_value_source, p_reason, p_reviewed_by
    FROM pg_constraint c WHERE c.conrelid = p_rel AND c.conname = p_conname
    ON CONFLICT (rel, conname) DO UPDATE SET condef_md5 = excluded.condef_md5, value_source = excluded.value_source,
      reason = excluded.reason, reviewed_by = excluded.reviewed_by, reviewed_at = current_date;
  IF NOT FOUND THEN RAISE EXCEPTION 'exempt_constraint: % has no constraint %', p_rel, p_conname; END IF;
END $$;

-- SECURITY DEFINER functions are classified so the catalog check can say who may call them:
--   read      STABLE lookup, executable by the policy group or narrower
--   write     VOLATILE, executable by exactly one service role (p_writer)
--   jobs      import / sweep, executable by the jobs role only
--   prologue  called by the unit-of-work prologue before the role switch (the fence): VOLATILE, asserts the isolation level
CREATE TABLE kernel.function_registry (
  proc regprocedure PRIMARY KEY,
  class text NOT NULL CHECK (class IN ('read', 'write', 'jobs', 'prologue')),
  writer name,
  CHECK ((class = 'write') = (writer IS NOT NULL)));

CREATE FUNCTION kernel.register_function(p_proc regprocedure, p_class text, p_writer name DEFAULT NULL) RETURNS void
LANGUAGE sql SET search_path = pg_catalog, kernel, pg_temp AS $$
  INSERT INTO kernel.function_registry (proc, class, writer) VALUES (p_proc, p_class, p_writer)
  ON CONFLICT (proc) DO UPDATE SET class = excluded.class, writer = excluded.writer $$;

-- A restore window admits INSERTs of ONE org's rows, for a limited time (opened and closed by db.restore_window).
CREATE TABLE kernel.restore_window (
  one boolean PRIMARY KEY DEFAULT true CHECK (one),
  org text NOT NULL,
  until timestamptz NOT NULL,
  disabled_logins name[] NOT NULL DEFAULT '{}',   -- application logins switched off for the window
  sequences_restored boolean NOT NULL DEFAULT false);

CREATE FUNCTION kernel.restore_allows(p_org text) RETURNS boolean
LANGUAGE sql STABLE SET search_path = pg_catalog, kernel, pg_temp AS $$
  SELECT EXISTS (SELECT 1 FROM kernel.restore_window w WHERE w.org = p_org AND w.until > now()) $$;

-- Secure ONE relation (no recursion). Takes ACCESS EXCLUSIVE on that relation only.
CREATE FUNCTION kernel._secure_rel(p_part regclass, p_root regclass, p_class text, p_jobs boolean, p_vetted boolean,
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
    EXECUTE format('CREATE POLICY vetted_write ON %s TO %I USING (true) WITH CHECK (true)', p_part, v_vetted);
    IF is_root THEN EXECUTE format('GRANT SELECT, INSERT, UPDATE ON %s TO %I', p_part, v_vetted); END IF;
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

-- Whole tree: the migrate job and drift repair only. Locks every relation in the tree; bounded by lock_timeout.
-- Class global: no RLS and no app privileges; only vetted functions (and backup, read-only) reach the table.
CREATE FUNCTION kernel.apply_rls(p_rel regclass, p_class text, p_jobs boolean DEFAULT false, p_vetted boolean DEFAULT false,
                                 p_lock_timeout text DEFAULT '5s', p_vetted_write boolean DEFAULT false)
RETURNS int LANGUAGE plpgsql SET search_path = pg_catalog, kernel, pg_temp AS $$
DECLARE part regclass; n int := 0; s kernel.settings; v_vetted text;
BEGIN
  SELECT * INTO s FROM kernel.settings;
  IF p_class = 'internal' THEN PERFORM kernel.register_internal(p_rel); RETURN 0; END IF;
  IF p_class <> 'global' AND NOT EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = p_rel AND a.attname = 'org_id'
                                         AND a.atttypid = 'text'::regtype AND a.attnotnull AND NOT a.attisdropped) THEN
    RAISE EXCEPTION 'apply_rls: % needs a column org_id text NOT NULL', p_rel;
  END IF;
  PERFORM set_config('lock_timeout', p_lock_timeout, true);
  INSERT INTO kernel.rls_registry(rel, class, jobs_access, vetted_read, vetted_write) VALUES (p_rel, p_class, p_jobs, p_vetted, p_vetted_write)
    ON CONFLICT (rel) DO UPDATE SET class = excluded.class, jobs_access = excluded.jobs_access, vetted_read = excluded.vetted_read,
      vetted_write = excluded.vetted_write;
  IF p_class = 'global' THEN
    v_vetted := s.vetted_prefix || (SELECT n.nspname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.oid = p_rel);
    EXECUTE format('REVOKE ALL ON %s FROM PUBLIC, %I', p_rel, s.app_group);
    EXECUTE format('GRANT SELECT ON %s TO %I', p_rel, s.backup);       -- restore gets nothing: global rows load through vetted import functions
    EXECUTE format('GRANT USAGE ON SCHEMA %I TO %I', (SELECT n.nspname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                                                       WHERE c.oid = p_rel), s.backup);
    IF p_vetted_write THEN EXECUTE format('GRANT SELECT, INSERT, UPDATE ON %s TO %I', p_rel, v_vetted);
    ELSIF p_vetted THEN EXECUTE format('GRANT SELECT ON %s TO %I', p_rel, v_vetted); END IF;
    RETURN 0;
  END IF;
  -- pg_partition_tree() returns no rows for a plain table, so add the relation itself.
  FOR part IN SELECT p_rel UNION SELECT relid FROM pg_partition_tree(p_rel) LOOP
    PERFORM kernel._secure_rel(part, p_rel, p_class, p_jobs, p_vetted, p_vetted_write);
    n := n + 1;
  END LOOP;
  RETURN n;
END $$;

-- One new leaf BEFORE it is attached: locks only the leaf. The caller then runs ATTACH PARTITION
-- (SHARE UPDATE EXCLUSIVE on the parent: readers and writers continue).
CREATE FUNCTION kernel.apply_rls_partition(p_child regclass, p_root regclass, p_lock_timeout text DEFAULT '2s')
RETURNS void LANGUAGE plpgsql SET search_path = pg_catalog, kernel, pg_temp AS $$
DECLARE g kernel.rls_registry;
BEGIN
  SELECT * INTO g FROM kernel.rls_registry WHERE rel = p_root;
  IF g IS NULL OR g.class = 'global' THEN RAISE EXCEPTION 'apply_rls_partition: % is not a registered org-scoped root', p_root; END IF;
  PERFORM set_config('lock_timeout', p_lock_timeout, true);
  PERFORM kernel._secure_rel(p_child, p_root, g.class, g.jobs_access, g.vetted_read, g.vetted_write);
END $$;

-- Kernel internals are owner-only (the migrate role is a member). Explicit grants below.
REVOKE ALL ON FUNCTION kernel.register_internal(regclass), kernel.exempt_constraint(regclass, name, text, text, text), kernel.register_function(regprocedure, text, name),
  kernel.restore_allows(text), kernel._secure_rel(regclass, regclass, text, boolean, boolean, boolean),
  kernel.apply_rls(regclass, text, boolean, boolean, text, boolean), kernel.apply_rls_partition(regclass, regclass, text) FROM PUBLIC;
GRANT USAGE ON SCHEMA kernel TO pipeshub_restore;
GRANT SELECT ON kernel.restore_window TO pipeshub_restore;
GRANT EXECUTE ON FUNCTION kernel.restore_allows(text) TO pipeshub_restore;
