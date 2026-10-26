-- The vetted function framework: scope and content flag in the function registry, the checklist checks, the extension point's grant.
-- Additive: kernel.rls_violations keeps its earlier checks (as kernel.rls_violations_core) and gains kernel.vetted_violations.
ALTER TABLE kernel.function_registry
  ADD COLUMN scope text NOT NULL DEFAULT 'org' CHECK (scope IN ('org', 'global', 'lineage', 'helper')),
  ADD COLUMN content_ok boolean NOT NULL DEFAULT false;
ALTER TABLE kernel.function_registry ADD CONSTRAINT function_registry_helper_is_read CHECK (scope <> 'helper' OR class = 'read') NOT VALID;
ALTER TABLE kernel.function_registry VALIDATE CONSTRAINT function_registry_helper_is_read;

DROP FUNCTION kernel.register_function(regprocedure, text, name);
CREATE FUNCTION kernel.register_function(p_proc regprocedure, p_class text, p_writer name DEFAULT NULL,
                                         p_scope text DEFAULT 'org', p_content_ok boolean DEFAULT false) RETURNS void
LANGUAGE sql SET search_path = pg_catalog, kernel, pg_temp AS $$
  INSERT INTO kernel.function_registry (proc, class, writer, scope, content_ok) VALUES (p_proc, p_class, p_writer, p_scope, p_content_ok)
  ON CONFLICT (proc) DO UPDATE SET class = excluded.class, writer = excluded.writer, scope = excluded.scope, content_ok = excluded.content_ok $$;
REVOKE ALL ON FUNCTION kernel.register_function(regprocedure, text, name, text, boolean) FROM PUBLIC;

-- Tables an extra migration location lets the vetted owner of schema kernel read (the parent lookup's data). SELECT only; the grant is
-- also a line of the surface snapshot, so the location's allowlist must list it.
CREATE TABLE kernel.vetted_extension_reads (rel regclass PRIMARY KEY);
-- An extra location claims a NEW, empty schema first; only tables in claimed schemas can be read by the vetted owner of kernel.
CREATE TABLE kernel.extension_schemas (nsp name PRIMARY KEY);
CREATE FUNCTION kernel.declare_extension_schema(p_schema name) RETURNS void
LANGUAGE plpgsql SET search_path = pg_catalog, kernel, pg_temp AS $fn$
DECLARE s kernel.settings;
BEGIN
  SELECT * INTO s FROM kernel.settings;
  IF p_schema IN ('kernel', 'platform', 'ext', 'public', 'information_schema') OR p_schema::text LIKE 'pg\_%' THEN
    RAISE EXCEPTION 'declare_extension_schema: % is not available to an extension', p_schema;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = p_schema) THEN RAISE EXCEPTION 'declare_extension_schema: no schema %', p_schema; END IF;
  IF EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = p_schema)
     OR EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = p_schema)
     OR EXISTS (SELECT 1 FROM pg_roles WHERE rolname = s.vetted_prefix || p_schema) THEN
    RAISE EXCEPTION 'declare_extension_schema: % is not new (declare it before creating anything in it)', p_schema;
  END IF;
  INSERT INTO kernel.extension_schemas VALUES (p_schema) ON CONFLICT DO NOTHING;
END $fn$;
REVOKE ALL ON FUNCTION kernel.declare_extension_schema(name) FROM PUBLIC;
REVOKE ALL ON TABLE kernel.extension_schemas FROM PUBLIC;

CREATE FUNCTION kernel.grant_vetted_owner_select(p_rel regclass) RETURNS void
LANGUAGE plpgsql SET search_path = pg_catalog, kernel, pg_temp AS $fn$
DECLARE s kernel.settings; v name; sch name;
BEGIN
  SELECT * INTO s FROM kernel.settings;
  v := s.vetted_prefix || 'kernel';
  SELECT n.nspname INTO sch FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.oid = p_rel;
  IF NOT EXISTS (SELECT 1 FROM kernel.extension_schemas WHERE nsp = sch) THEN
    RAISE EXCEPTION 'grant_vetted_owner_select: schema % was not declared by this extension (kernel.declare_extension_schema)', sch;
  END IF;
  INSERT INTO kernel.vetted_extension_reads VALUES (p_rel) ON CONFLICT DO NOTHING;
  EXECUTE format('GRANT USAGE ON SCHEMA %I TO %I', sch, v);
  EXECUTE format('GRANT SELECT ON %s TO %I', p_rel, v);
END $fn$;
REVOKE ALL ON FUNCTION kernel.grant_vetted_owner_select(regclass) FROM PUBLIC;
REVOKE ALL ON TABLE kernel.vetted_extension_reads FROM PUBLIC;

CREATE VIEW kernel.vetted_violations WITH (security_invoker = true) AS
WITH s AS (SELECT * FROM kernel.settings),
nsp AS (SELECT n.oid, n.nspname FROM pg_namespace n
  WHERE n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast') AND n.nspname NOT LIKE 'pg_temp%' AND n.nspname NOT LIKE 'pg_toast_temp%'),
app_nsp AS (SELECT * FROM nsp WHERE nspname <> 'kernel'),
ext_objs AS (  -- members of an ALLOWLISTED extension only; anything added to another extension stays visible
  SELECT d.objid FROM pg_depend d JOIN pg_extension e ON e.oid = d.refobjid, s
  WHERE d.deptype = 'e' AND d.refclassid = 'pg_extension'::regclass AND d.classid IN ('pg_class'::regclass, 'pg_proc'::regclass)
    AND e.extname = ANY (s.extensions)),
grp AS (SELECT r.oid FROM pg_roles r, s WHERE r.rolname = s.app_group),
svc AS (SELECT r.oid FROM pg_roles r, s WHERE r.rolname = ANY (s.services)),               -- the NOLOGIN service roles
logins AS (  -- during a restore window the application logins are switched off, and are still logins
  SELECT oid, rolname FROM pg_roles
  WHERE rolcanlogin OR rolname = ANY (SELECT unnest(disabled_logins) FROM kernel.restore_window)),
admin_roles AS (SELECT r.oid FROM pg_roles r, s WHERE r.rolname = ANY (s.admins)),            -- reviewed administrative roles
trusted AS (SELECT r.oid FROM pg_roles r, s WHERE r.rolname IN (s.owner, s.migrate, s.backup, s.restore) OR r.rolname LIKE s.vetted_prefix || '%'),
rall AS (SELECT r.oid FROM pg_roles r WHERE NOT r.rolsuper AND r.rolname NOT LIKE 'pg\_%'),   -- EVERY role, whatever its name
actors AS (SELECT oid FROM rall WHERE oid NOT IN (SELECT oid FROM trusted) AND oid NOT IN (SELECT oid FROM admin_roles)),
reach AS (  -- (actor, role): role is reachable from actor by ANY membership (inherit or SET), all_inherit = every hop inherits
  WITH RECURSIVE t(actor, role, all_inherit) AS (
    SELECT a.oid, a.oid, true FROM rall a
    UNION SELECT t.actor, m.roleid, t.all_inherit AND m.inherit_option FROM t JOIN pg_auth_members m ON m.member = t.role)
  SELECT * FROM t),
reach_roles AS (SELECT DISTINCT role AS oid FROM reach WHERE actor IN (SELECT oid FROM actors)),
vetted_roles AS (SELECT r.oid, r.rolname FROM pg_roles r, s WHERE r.rolname LIKE s.vetted_prefix || '%'),
rels AS (
  SELECT c.oid::regclass AS rel, c.oid, c.relname, n.nspname, c.relkind, c.relispartition, c.relrowsecurity, c.relforcerowsecurity, c.relowner, c.relacl,
         (SELECT g.rel FROM kernel.rls_registry g WHERE c.oid = g.rel OR c.oid IN (SELECT relid FROM pg_partition_tree(g.rel)) LIMIT 1) AS root
  FROM pg_class c JOIN app_nsp n ON n.oid = c.relnamespace WHERE c.relkind IN ('r', 'p', 'f', 'm')),
reg AS (SELECT rels.*, g.class, g.jobs_access, g.vetted_read, g.vetted_write FROM rels JOIN kernel.rls_registry g ON g.rel = rels.root WHERE g.class NOT IN ('global', 'internal')),
glob AS (SELECT rels.* FROM rels JOIN kernel.rls_registry g ON g.rel = rels.root WHERE g.class = 'global'),
exempt AS (SELECT e.*, c.oid AS conoid, c.conindid, md5(pg_get_constraintdef(c.oid)) AS cur_md5
           FROM kernel.rls_exemptions e LEFT JOIN pg_constraint c ON c.conrelid = e.rel AND c.conname = e.conname),
expected AS (
  SELECT reg.oid, t.name, t.cmd, t.qual, t.with_check,
         CASE t.name WHEN 'jobs_all' THEN s.jobs WHEN 'vetted_read' THEN s.vetted_prefix || reg.nspname
                     WHEN 'vetted_write' THEN s.vetted_prefix || reg.nspname
                     WHEN 'backup_read' THEN s.backup WHEN 'restore_insert' THEN s.restore ELSE s.app_group END AS role
  FROM reg, s, kernel.policy_template t
  WHERE t.name IN ('org_scope', 'backup_read', 'restore_insert') OR (t.name = 'lineage_read' AND reg.class = 'lineage')
     OR (t.name = 'jobs_all' AND reg.jobs_access) OR (t.name = 'vetted_read' AND reg.vetted_read)
     OR (t.name = 'vetted_write' AND reg.vetted_write)),
actual AS (
  SELECT p.polrelid AS oid, p.polname AS name, p.polcmd AS cmd, p.polpermissive,
         pg_get_expr(p.polqual, p.polrelid) AS qual, pg_get_expr(p.polwithcheck, p.polrelid) AS with_check,
         (SELECT array_agg(rolname ORDER BY rolname) FROM pg_roles WHERE oid = ANY (p.polroles)) AS roles
  FROM pg_policy p),
fn AS (  -- every SECURITY DEFINER function, with its registry class and effective ACL
  SELECT p.oid, p.oid::regprocedure::text AS name, p.proname, p.provolatile, p.proowner, p.prosrc, n.nspname, g.class, g.writer,
         g.scope, g.content_ok, p.proconfig, p.pronargs, p.proretset, p.prolang, p.proargnames, p.proargmodes, p.prorettype,
         p.proargtypes::oid[] AS argtypes, COALESCE(p.proacl, acldefault('f', p.proowner)) AS acl
  FROM pg_proc p JOIN nsp n ON n.oid = p.pronamespace LEFT JOIN kernel.function_registry g ON g.proc = p.oid
  WHERE p.prosecdef),                                   -- extension members are NOT exempt: anything added to an extension is still checked
-- who can run a function: any role (not only ours) with an ACL entry for it or for a role it can reach. PUBLIC is reported separately.
can_exec AS (
  SELECT fn.oid AS fn, r.oid AS role FROM fn, (SELECT oid FROM actors UNION SELECT r2.oid FROM pg_roles r2, s WHERE r2.rolname IN (s.backup, s.restore)) r
  WHERE EXISTS (SELECT 1 FROM aclexplode(fn.acl) x WHERE x.privilege_type = 'EXECUTE' AND x.grantee <> 0
                AND x.grantee IN (SELECT role FROM reach WHERE actor = r.oid))),
cat AS (
-- ---- vetted function checklist (what the catalog can see of it; the rest is the generated test suite) ----
-- 55 (a SECURITY DEFINER body is SQL or PL/pgSQL: no language that reaches outside the database)
SELECT '55 SECURITY DEFINER function language is ' || l.lanname AS rule, fn.name AS object FROM fn JOIN pg_language l ON l.oid = fn.prolang
  WHERE l.lanname NOT IN ('sql', 'plpgsql')
UNION ALL -- 56 (checklist 1: static SQL only, no EXECUTE and no format() into SQL)
SELECT '56 read/write function builds SQL dynamically', fn.name FROM fn
  WHERE fn.class IN ('read', 'write') AND fn.prosrc ~* '\mexecute\M|\mformat\s*\(|\m(query|cursor|table|schema|database)_to_xml\w*'
UNION ALL -- 57 (vetted write functions have no conflict path: it would be an existence oracle)
SELECT '57 write function uses ON CONFLICT', fn.name FROM fn WHERE fn.class = 'write' AND fn.prosrc ~* '\mon\s+conflict\M'
UNION ALL -- 58 (checklist 2: the path holds pg_catalog, the function's own schema, ext and pg_temp, nothing else)
SELECT '58 SECURITY DEFINER search_path lists ' || x.e, fn.name FROM fn,
  LATERAL (SELECT unnest(string_to_array(substr(cfg, 13), ', ')) AS e FROM unnest(fn.proconfig) cfg WHERE cfg LIKE 'search_path=%') x
  WHERE x.e NOT IN ('pg_catalog', 'pg_temp', 'ext', fn.nspname)
UNION ALL -- 59 (checklist 3: the org comes from the GUC)
SELECT '59 org-scoped function does not read app.org_id', fn.name FROM fn
  WHERE fn.class IN ('read', 'write') AND fn.scope IN ('org', 'lineage') AND fn.prosrc !~* 'current_setting\s*\(\s*''app\.org_id'''
UNION ALL
SELECT '60 org-scoped function takes an org argument ' || a.name, fn.name FROM fn, LATERAL unnest(fn.proargnames) WITH ORDINALITY a(name, i)
  WHERE fn.class IN ('read', 'write') AND fn.scope = 'org' AND (a.name ~* '(^|_)(org|organi[sz]ation|workspace|account)(_?id)?($|_)' OR fn.prosrc ~* ('org_id\s*=\s*' || a.name || '\M')) AND COALESCE(fn.proargmodes[a.i], 'i') IN ('i', 'b', 'v')
UNION ALL -- 61 (global lookups: exact key only, no LIKE / regex / prefix)
SELECT '61 global function matches by pattern, not by exact key', fn.name FROM fn
  WHERE fn.scope = 'global' AND fn.class IN ('read', 'write') AND fn.prosrc ~* '\m(i?like|similar\s+to)\M|\s!?~\*?\s'
UNION ALL -- 62 (checklist 4: every argument is validated, errors are SQLSTATE 22023)
SELECT '62 read/write function does not validate its arguments (22023)', fn.name FROM fn
  WHERE fn.class IN ('read', 'write') AND fn.pronargs > 0 AND fn.prosrc !~ '22023'
UNION ALL -- 63, 64 (checklist 5: minimal columns, no whole rows, no content column unless registered content_ok)
SELECT '63 function returns a whole table row', fn.name FROM fn
  WHERE fn.class IN ('read', 'write') AND EXISTS (SELECT 1 FROM pg_type t JOIN pg_class c ON c.oid = t.typrelid WHERE t.oid = fn.prorettype AND c.relkind IN ('r', 'p', 'v', 'm', 'f'))
UNION ALL
SELECT '63 function builds json or a row value, which hides whole rows', fn.name FROM fn
  WHERE fn.class IN ('read', 'write') AND NOT COALESCE(fn.content_ok, false)
    AND (fn.prorettype IN ('json'::regtype, 'jsonb'::regtype) OR fn.prosrc ~* '\m(to_jsonb?|row_to_json|json_agg|jsonb_agg|json_build_object|jsonb_build_object|row)\M\s*\(')
UNION ALL
SELECT '64 function returns a content column ' || a.name, fn.name FROM fn, LATERAL unnest(fn.proargnames) WITH ORDINALITY a(name, i)
  WHERE fn.class IN ('read', 'write') AND NOT COALESCE(fn.content_ok, false) AND fn.proargmodes[a.i] IN ('t', 'o')
    AND a.name ~* '^(body|content|text|html|markdown|payload|data|raw|blob|document)$'
UNION ALL -- 65 (checklist 6: a set-returning read function has a hard LIMIT)
SELECT '65 set-returning read function has no LIMIT', fn.name FROM fn WHERE fn.class = 'read' AND fn.proretset AND fn.prosrc !~* '\mlimit\M'
UNION ALL -- 66 (checklist 7: a read function is for the application, never for jobs, backup or restore)
SELECT '66 read-class function executable by ' || x.role::regrole::text, fn.name FROM fn JOIN can_exec x ON x.fn = fn.oid, s
  WHERE fn.class = 'read' AND fn.scope <> 'helper' AND x.role IN (SELECT oid FROM pg_roles WHERE rolname IN (s.jobs, s.backup, s.restore))
UNION ALL -- 67 (checklist 11: nothing that raises on row data: casts, division, date parsing, jsonb paths)
SELECT '67 function body can raise on row data', fn.name FROM fn
  WHERE fn.class IN ('read', 'write') AND fn.prosrc ~* '::\s*(?!(text|varchar|name)\M)|\mcast\s*\(|\mto_(date|timestamp|number|char)\M|\mjsonb?_path|\m(int[248]|float[48]|numeric|bool|date|timestamptz?|uuid|jsonb?)\s*\(|[\w)\]]\s*/\s*[\w(]|\w\s*%\s*[\w(]'
UNION ALL -- 68 (the lineage functions keep their frozen shape: only a body may be replaced by an extra migration location)
SELECT '68 kernel.' || f.name || ' is missing or changed in shape', 'kernel.' || f.name FROM (VALUES ('org_parent_ids', 'helper'), ('org_lineage', 'lineage')) f(name, scope)
  WHERE NOT EXISTS (SELECT 1 FROM fn WHERE fn.nspname = 'kernel' AND fn.proname = f.name AND array_to_string(fn.argtypes, ',') = 'text'::regtype::oid::text AND fn.prorettype = 'text[]'::regtype
                      AND fn.provolatile = 's' AND fn.class = 'read' AND fn.scope = f.scope AND fn.pronargs = 1)
UNION ALL -- 70 (a built-in function never runs with its owner's rights or carries settings)
SELECT '70 built-in function is SECURITY DEFINER or has settings', p.oid::regprocedure::text FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
  WHERE n.nspname IN ('pg_catalog', 'information_schema') AND p.oid < 16384 AND (p.prosecdef OR p.proconfig IS NOT NULL)
UNION ALL -- 72 (a helper is called by other vetted functions only)
SELECT '72 helper function executable by ' || x.role::regrole::text, fn.name FROM fn JOIN can_exec x ON x.fn = fn.oid WHERE fn.scope = 'helper' AND x.role <> fn.proowner
UNION ALL
SELECT '72 helper function executable by ' || a.grantee::regrole::text, fn.name FROM fn, LATERAL aclexplode(fn.acl) a
  WHERE fn.scope = 'helper' AND a.privilege_type = 'EXECUTE' AND a.grantee <> fn.proowner AND a.grantee IN (SELECT oid FROM vetted_roles)
UNION ALL -- 73 (checklist 8: a vetted role reads only tables that opted in with vetted_read / vetted_write)
SELECT '73 vetted role ' || a.grantee::regrole::text || ' holds ' || a.privilege_type || ' on a table that did not opt in', c.oid::regclass::text
  FROM pg_class c JOIN nsp n ON n.oid = c.relnamespace JOIN kernel.rls_registry r ON r.rel = c.oid, LATERAL aclexplode(c.relacl) a
  WHERE a.grantee IN (SELECT oid FROM vetted_roles) AND a.privilege_type IN ('SELECT', 'INSERT', 'UPDATE') AND NOT (r.vetted_read OR r.vetted_write)
    AND NOT (a.privilege_type = 'SELECT' AND c.oid IN (SELECT rel FROM kernel.vetted_extension_reads) AND a.grantee = to_regrole((SELECT vetted_prefix FROM kernel.settings) || 'kernel'))

UNION ALL -- 74 (no side channel: RAISE only as a constant EXCEPTION with an SQLSTATE; a notice, a formatted message or DETAIL could carry another org's value)
SELECT '74 function raises other than a constant EXCEPTION with an SQLSTATE', fn.name FROM fn
  WHERE fn.class IN ('read', 'write', 'jobs') AND (
    EXISTS (SELECT 1 FROM regexp_matches(fn.prosrc, '\mraise\M[^;]*', 'gi') AS m(x)
            WHERE x[1] !~* '^raise\s+exception\s+''[^''%]*''\s+using\s+errcode\s*=\s*''[0-9A-Z]{5}''\s*$')
    OR fn.prosrc ~* '\massert\M')
UNION ALL -- 75 (the extension point's reads stay in schemas the extra location declared)
SELECT '75 vetted-owner read outside a declared extension schema', v.rel::text FROM kernel.vetted_extension_reads v
  WHERE NOT EXISTS (SELECT 1 FROM kernel.extension_schemas e JOIN pg_class c ON c.oid = v.rel JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = e.nsp)
)
SELECT rule, object FROM cat;
REVOKE ALL ON kernel.vetted_violations FROM PUBLIC;

-- The earlier view keeps its name as the core; the public name is core plus the vetted checks. Check 42 (a vetted role holds
-- privileges in its own schema only) is waived for the one reviewed case: SELECT granted through grant_vetted_owner_select.
DO $$ BEGIN EXECUTE 'ALTER VIEW kernel.rls_violations RENAME TO rls_violations_core'; END $$;
CREATE VIEW kernel.rls_violations WITH (security_invoker = true) AS
SELECT c.rule, c.object FROM kernel.rls_violations_core c
  WHERE NOT (c.rule = '42 vetted role pipeshub_vetted_kernel holds SELECT outside its schema' AND c.object IN (SELECT rel::text FROM kernel.vetted_extension_reads))
UNION ALL
SELECT rule, object FROM kernel.vetted_violations;
REVOKE ALL ON kernel.rls_violations FROM PUBLIC;

-- The jobs login runs write units too: its prologue calls the fence before anything else, so it needs what the service logins got in
-- kernel_0007 (USAGE on kernel, EXECUTE on the fence) and what the fence reads (the one profile row), nothing more.
GRANT USAGE ON SCHEMA kernel, platform TO pipeshub_jobs;
GRANT EXECUTE ON FUNCTION kernel.assert_profile_epoch(int) TO pipeshub_jobs;
GRANT SELECT ON platform.deployment_profile TO pipeshub_jobs;
