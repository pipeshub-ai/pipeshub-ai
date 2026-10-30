-- The import role: the one login that copies an existing install's documents into PostgreSQL (docstore-migrate), and nothing else. It
-- holds no privilege of its own on any table until a revision grants it, and it has a password only while an operator is moving data
-- (the move script enables the login for the length of the run). Its row policy is `import_all`, present only on the tables that
-- kernel.grant_import_access names (the tables the copy writes); the jobs role and the application roles gain nothing from it.

-- The role is normally created by the deployment bootstrap (python -m db.bootstrap_roles); created here, NOLOGIN, only when missing.
RESET ROLE;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pipeshub_import') THEN
    CREATE ROLE pipeshub_import NOLOGIN;
  END IF;
EXCEPTION WHEN insufficient_privilege THEN
  RAISE EXCEPTION 'the import role is missing and this role cannot create it: run the role bootstrap (python -m db.bootstrap_roles)';
END $$;
SET LOCAL ROLE pipeshub_owner;

ALTER TABLE kernel.settings ADD COLUMN importer name NOT NULL DEFAULT 'pipeshub_import';
ALTER TABLE kernel.rls_registry ADD COLUMN import_access boolean NOT NULL DEFAULT false;
INSERT INTO kernel.policy_template VALUES ('import_all', '*', 'true', 'true');

-- Secure ONE relation for the import role (no recursion): the policy, nothing else.
CREATE FUNCTION kernel._import_policy(p_part regclass) RETURNS void
LANGUAGE plpgsql SET search_path = pg_catalog, kernel, pg_temp AS $fn$
DECLARE s kernel.settings;
BEGIN
  SELECT * INTO s FROM kernel.settings;
  EXECUTE format('DROP POLICY IF EXISTS import_all ON %s', p_part);
  EXECUTE format('CREATE POLICY import_all ON %s TO %I USING (true) WITH CHECK (true)', p_part, s.importer);
END $fn$;

-- Gives the import role the row policy on a registered org-scoped table and every partition of it (privileges are granted separately).
CREATE FUNCTION kernel.grant_import_access(p_rel regclass) RETURNS int
LANGUAGE plpgsql SET search_path = pg_catalog, kernel, pg_temp AS $fn$
DECLARE part regclass; n int := 0;
BEGIN
  UPDATE kernel.rls_registry SET import_access = true WHERE rel = p_rel AND class IN ('org', 'lineage');
  IF NOT FOUND THEN RAISE EXCEPTION 'grant_import_access: % is not a registered org-scoped table (kernel.apply_rls first)', p_rel; END IF;
  FOR part IN SELECT p_rel UNION SELECT relid FROM pg_partition_tree(p_rel) LOOP
    PERFORM kernel._import_policy(part);
    n := n + 1;
  END LOOP;
  RETURN n;
END $fn$;

-- A new leaf carries the import policy when its root has it (same body as before, plus that).
CREATE OR REPLACE FUNCTION kernel.apply_rls_partition(p_child regclass, p_root regclass, p_lock_timeout text DEFAULT '2s')
RETURNS void LANGUAGE plpgsql SET search_path = pg_catalog, kernel, pg_temp AS $fn$
DECLARE g kernel.rls_registry;
BEGIN
  SELECT * INTO g FROM kernel.rls_registry WHERE rel = p_root;
  IF g IS NULL OR g.class = 'global' THEN RAISE EXCEPTION 'apply_rls_partition: % is not a registered org-scoped root', p_root; END IF;
  PERFORM set_config('lock_timeout', p_lock_timeout, true);
  PERFORM kernel._secure_rel(p_child, p_root, g.class, g.jobs_access, g.vetted_read, g.vetted_write);
  IF g.import_access THEN PERFORM kernel._import_policy(p_child); END IF;
END $fn$;
REVOKE ALL ON FUNCTION kernel._import_policy(regclass), kernel.grant_import_access(regclass) FROM PUBLIC;

-- The catalog check knows the policy: it expects import_all on a table registered with import_access, for the import role, and on no other.
DO $$
DECLARE core text; wrapper text; before text;
BEGIN
  wrapper := pg_get_viewdef('kernel.rls_violations'::regclass);
  core := pg_get_viewdef('kernel.rls_violations_core'::regclass);
  before := core;
  core := replace(core, E'            settings.org_id_pattern\n', E'            settings.org_id_pattern,\n            settings.importer\n');
  IF core = before THEN RAISE EXCEPTION 'kernel_0012: the settings columns were not found in the catalog view'; END IF;
  before := core;
  core := replace(core, E'            g.vetted_write\n           FROM (rels', E'            g.vetted_write,\n            g.import_access\n           FROM (rels');
  IF core = before THEN RAISE EXCEPTION 'kernel_0012: the registry columns were not found in the catalog view'; END IF;
  before := core;
  core := replace(core, E'                    WHEN ''jobs_all''::text THEN s.jobs\n', E'                    WHEN ''jobs_all''::text THEN s.jobs\n                    WHEN ''import_all''::text THEN s.importer\n');
  IF core = before THEN RAISE EXCEPTION 'kernel_0012: the policy roles were not found in the catalog view'; END IF;
  before := core;
  core := replace(core, ' OR ((t.name = ''jobs_all''::text) AND reg.jobs_access)', ' OR ((t.name = ''jobs_all''::text) AND reg.jobs_access) OR ((t.name = ''import_all''::text) AND reg.import_access)');
  IF core = before THEN RAISE EXCEPTION 'kernel_0012: the expected policies were not found in the catalog view'; END IF;
  before := core;  -- check 31: a jobs-only function may be executable by the jobs role and by the import role
  core := replace(core, E'AND (x.role IS DISTINCT FROM (to_regrole((s.jobs)::text))::oid))\n        UNION ALL\n         SELECT ''31 import/sweep',
                        E'AND (x.role IS DISTINCT FROM (to_regrole((s.jobs)::text))::oid) AND (x.role IS DISTINCT FROM (to_regrole((s.importer)::text))::oid))\n        UNION ALL\n         SELECT ''31 import/sweep');
  IF core = before THEN RAISE EXCEPTION 'kernel_0012: check 31 was not found in the catalog view'; END IF;
  DROP VIEW kernel.rls_violations;
  DROP VIEW kernel.rls_violations_core;
  EXECUTE 'CREATE VIEW kernel.rls_violations_core WITH (security_invoker = true) AS ' || core;
  EXECUTE 'CREATE VIEW kernel.rls_violations WITH (security_invoker = true) AS ' || wrapper;
  REVOKE ALL ON kernel.rls_violations_core, kernel.rls_violations FROM PUBLIC;
END $$;
