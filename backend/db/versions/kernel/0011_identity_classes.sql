-- Person-level tables and functions, jobs-only import/sweep functions, the external-issuer dedupe exception, the fence barrier,
-- the advisory-lock key registry and shared-sequence waivers. Additive: the earlier checks stay, kernel.person_violations is
-- folded into kernel.rls_violations.

-- Registry flags. person: a global table keyed by a person (no org_id); only the vetted person functions reach it.
-- vetted_delete: the schema's vetted owner may DELETE from this global table (sweep functions only; the checks enforce it).
ALTER TABLE kernel.rls_registry ADD COLUMN person boolean NOT NULL DEFAULT false, ADD COLUMN vetted_delete boolean NOT NULL DEFAULT false;
ALTER TABLE kernel.rls_registry ADD CONSTRAINT rls_registry_person_is_global CHECK (class = 'global' OR NOT (person OR vetted_delete)) NOT VALID;
ALTER TABLE kernel.rls_registry VALIDATE CONSTRAINT rls_registry_person_is_global;

CREATE FUNCTION kernel.mark_person(p_rel regclass) RETURNS void
LANGUAGE plpgsql SET search_path = pg_catalog, kernel, pg_temp AS $fn$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = p_rel AND a.attname = 'org_id' AND NOT a.attisdropped) THEN
    RAISE EXCEPTION 'mark_person: % has an org_id column; a person table is keyed by the person alone', p_rel;
  END IF;
  UPDATE kernel.rls_registry SET person = true WHERE rel = p_rel AND class = 'global';
  IF NOT FOUND THEN RAISE EXCEPTION 'mark_person: % is not a registered global table (kernel.apply_rls first)', p_rel; END IF;
END $fn$;

CREATE FUNCTION kernel.allow_vetted_delete(p_rel regclass) RETURNS void
LANGUAGE plpgsql SET search_path = pg_catalog, kernel, pg_temp AS $fn$
DECLARE s kernel.settings;
BEGIN
  SELECT * INTO s FROM kernel.settings;
  UPDATE kernel.rls_registry SET vetted_delete = true WHERE rel = p_rel AND class = 'global';
  IF NOT FOUND THEN RAISE EXCEPTION 'allow_vetted_delete: % is not a registered global table', p_rel; END IF;
  EXECUTE format('GRANT DELETE ON %s TO %I', p_rel,
                 s.vetted_prefix || (SELECT n.nspname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.oid = p_rel));
END $fn$;

-- Function registry: the person-level effect, and the dedupe table a function may answer "already processed" for.
ALTER TABLE kernel.function_registry ADD COLUMN person_effect text, ADD COLUMN dedupe_rel regclass;
ALTER TABLE kernel.function_registry
  ADD CONSTRAINT function_registry_person_effect CHECK (person_effect IS NULL OR (person_effect = 'read' AND class = 'read')
    OR (person_effect IN ('self', 'admin', 'preorg') AND class = 'write')) NOT VALID,
  ADD CONSTRAINT function_registry_dedupe_is_write CHECK (dedupe_rel IS NULL OR class = 'write') NOT VALID;
ALTER TABLE kernel.function_registry VALIDATE CONSTRAINT function_registry_person_effect;
ALTER TABLE kernel.function_registry VALIDATE CONSTRAINT function_registry_dedupe_is_write;

CREATE FUNCTION kernel.annotate_function(p_proc regprocedure, p_person_effect text DEFAULT NULL, p_dedupe regclass DEFAULT NULL) RETURNS void
LANGUAGE plpgsql SET search_path = pg_catalog, kernel, pg_temp AS $fn$
BEGIN
  UPDATE kernel.function_registry SET person_effect = p_person_effect, dedupe_rel = p_dedupe WHERE proc = p_proc;
  IF NOT FOUND THEN RAISE EXCEPTION 'annotate_function: % is not registered', p_proc; END IF;
END $fn$;

-- Advisory-lock classes: a lock key is (registered class, object); one shared constant would let any component block every other.
CREATE TABLE kernel.lock_keys (class_id int PRIMARY KEY CHECK (class_id > 0), name text NOT NULL UNIQUE, purpose text NOT NULL);
INSERT INTO kernel.lock_keys VALUES
  (7340, 'docstore_fence', 'the document store cutover barrier'),
  (7341, 'profile_fence', 'the deployment profile barrier taken by the unit-of-work fence'),
  (7350, 'person_email', 'one lock per email for every write to a person-level table');

-- Shared sequences feed internal surrogates only. A sequence whose value is visible in a text column (a slug, a number in a name)
-- reveals the install-wide creation count, so each one is a reviewed waiver, written by a migration.
CREATE TABLE kernel.sequence_waivers (
  seq regclass PRIMARY KEY, rel regclass NOT NULL, attname name NOT NULL,
  reason text NOT NULL, reviewed_by text NOT NULL, reviewed_at date NOT NULL DEFAULT current_date);
CREATE FUNCTION kernel.waive_sequence(p_seq regclass, p_rel regclass, p_column name, p_reason text, p_reviewed_by text) RETURNS void
LANGUAGE plpgsql SET search_path = pg_catalog, kernel, pg_temp AS $fn$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_attrdef d JOIN pg_attribute a ON a.attrelid = d.adrelid AND a.attnum = d.adnum
                 JOIN pg_depend dp ON dp.classid = 'pg_attrdef'::regclass AND dp.objid = d.oid AND dp.refclassid = 'pg_class'::regclass AND dp.refobjid = p_seq
                 WHERE d.adrelid = p_rel AND a.attname = p_column) THEN
    RAISE EXCEPTION 'waive_sequence: %.% does not take its default from %', p_rel, p_column, p_seq;
  END IF;
  INSERT INTO kernel.sequence_waivers (seq, rel, attname, reason, reviewed_by) VALUES (p_seq, p_rel, p_column, p_reason, p_reviewed_by)
    ON CONFLICT (seq) DO UPDATE SET rel = excluded.rel, attname = excluded.attname, reason = excluded.reason, reviewed_by = excluded.reviewed_by,
                                    reviewed_at = current_date;
END $fn$;

-- A job-gated import function reads platform.jobs; its schema's vetted owner gets SELECT on that one table through this function,
-- which records the grant (checks 42 and 73 waive exactly the recorded grants).
CREATE TABLE kernel.job_gate_grants (owner name PRIMARY KEY, rel text NOT NULL DEFAULT 'platform.jobs' CHECK (rel = 'platform.jobs'));
CREATE FUNCTION kernel.grant_job_gate(p_schema name) RETURNS void
LANGUAGE plpgsql SET search_path = pg_catalog, kernel, pg_temp AS $fn$
DECLARE s kernel.settings; v name;
BEGIN
  SELECT * INTO s FROM kernel.settings;
  v := s.vetted_prefix || p_schema;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = v) THEN RAISE EXCEPTION 'grant_job_gate: no vetted role %', v; END IF;
  INSERT INTO kernel.job_gate_grants (owner) VALUES (v) ON CONFLICT (owner) DO NOTHING;
  EXECUTE format('GRANT USAGE ON SCHEMA platform TO %I', v);
  EXECUTE format('GRANT SELECT ON platform.jobs TO %I', v);
END $fn$;

-- The jobs table the import gate reads. Job state (status) is written by the migrator and the owner only: no application or jobs role holds
-- a privilege on it (check 12), so the role that imports cannot start the job that allows it.
CREATE TABLE IF NOT EXISTS platform.jobs (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, kind text NOT NULL,
  status text NOT NULL CHECK (status IN ('queued', 'running', 'done', 'failed', 'cancelled')),
  progress jsonb NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now());
SELECT kernel.register_internal('platform.jobs');

-- The fence reads platform.deployment_profile as the vetted owner of schema kernel; the service roles no longer read it themselves.
INSERT INTO kernel.extension_schemas VALUES ('platform') ON CONFLICT DO NOTHING;
INSERT INTO kernel.vetted_extension_reads VALUES ('platform.deployment_profile') ON CONFLICT DO NOTHING;
GRANT USAGE ON SCHEMA platform TO pipeshub_vetted_kernel;
GRANT SELECT ON platform.deployment_profile TO pipeshub_vetted_kernel;
REVOKE SELECT ON platform.deployment_profile FROM pipeshub_node, pipeshub_connectors, pipeshub_indexing, pipeshub_query, pipeshub_jobs;

-- Boot reads the epoch through a definer reader, since the roles no longer read the table. Hand-written in vetted/ (the table is in
-- another schema, so it has no manifest entry); executable by the service roles only.

REVOKE ALL ON FUNCTION kernel.mark_person(regclass), kernel.allow_vetted_delete(regclass), kernel.annotate_function(regprocedure, text, regclass),
  kernel.waive_sequence(regclass, regclass, name, text, text), kernel.grant_job_gate(name) FROM PUBLIC;
REVOKE ALL ON TABLE kernel.lock_keys, kernel.sequence_waivers, kernel.job_gate_grants FROM PUBLIC;

-- ONE normaliser for every text check below: comments removed, string literals blanked (keep_strings keeps them, for the few checks that
-- read a literal), whitespace collapsed, spaces around dots removed. A construct it cannot read is refused by check 98 (dollar quotes, E'' and
-- U& strings, quoted identifiers, MERGE, ONLY, FETCH, OFFSET), not taught to each check. lib/vetted.py has the same function (normalise);
-- a test keeps the two equal. Owner-only: only the migrate and owner roles read the catalog views.
CREATE FUNCTION kernel.src_text(p_src text, p_keep_strings boolean DEFAULT false) RETURNS text
LANGUAGE sql IMMUTABLE SET search_path = pg_catalog, pg_temp AS $fn$
  SELECT regexp_replace(regexp_replace(
    CASE WHEN p_keep_strings THEN x.s ELSE regexp_replace(x.s, '''(?:[^'']|'''')*''', '''''', 'g') END,
    '[ \t\n\r\f\v]*\.[ \t\n\r\f\v]*', '.', 'g'), '[ \t\n\r\f\v]+', ' ', 'g')
  FROM (SELECT regexp_replace(p_src, '(''(?:[^'']|'''')*'')|--[^\n]*|/\*([^*]|\*+[^*/])*\*+/', ' \1', 'g') AS s) x
$fn$;
REVOKE ALL ON FUNCTION kernel.src_text(text, boolean) FROM PUBLIC;

CREATE VIEW kernel.person_violations WITH (security_invoker = true) AS
WITH RECURSIVE s AS (SELECT * FROM kernel.settings),
nsp AS (SELECT n.oid, n.nspname FROM pg_namespace n
  WHERE n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast') AND n.nspname NOT LIKE 'pg_temp%' AND n.nspname NOT LIKE 'pg_toast_temp%'),
admin_roles AS (SELECT r.oid FROM pg_roles r, s WHERE r.rolname = ANY (s.admins)),
trusted AS (SELECT r.oid FROM pg_roles r, s WHERE r.rolname IN (s.owner, s.migrate, s.backup, s.restore) OR r.rolname LIKE s.vetted_prefix || '%'),
rall AS (SELECT r.oid FROM pg_roles r WHERE NOT r.rolsuper AND r.rolname NOT LIKE 'pg\_%'),
actors AS (SELECT oid FROM rall WHERE oid NOT IN (SELECT oid FROM trusted) AND oid NOT IN (SELECT oid FROM admin_roles)),
reach AS (
  WITH RECURSIVE t(actor, role) AS (
    SELECT a.oid, a.oid FROM rall a UNION SELECT t.actor, m.roleid FROM t JOIN pg_auth_members m ON m.member = t.role)
  SELECT * FROM t),
reach_roles AS (SELECT DISTINCT role AS oid FROM reach WHERE actor IN (SELECT oid FROM actors)),
vetted_roles AS (SELECT r.oid, r.rolname FROM pg_roles r, s WHERE r.rolname LIKE s.vetted_prefix || '%'),
glob AS (SELECT c.oid, c.relname, n.nspname, g.person, g.vetted_delete, g.vetted_write
         FROM kernel.rls_registry g JOIN pg_class c ON c.oid = g.rel JOIN pg_namespace n ON n.oid = c.relnamespace WHERE g.class = 'global'),
person_views AS (  -- views (and views over views, materialized ones too) that depend on a person table
  SELECT w.ev_class AS oid, d.refobjid AS base FROM pg_depend d JOIN pg_rewrite w ON w.oid = d.objid
    WHERE d.classid = 'pg_rewrite'::regclass AND d.refclassid = 'pg_class'::regclass AND d.refobjid IN (SELECT oid FROM glob WHERE person) AND w.ev_class <> d.refobjid
  UNION
  SELECT w.ev_class, pv.base FROM person_views pv JOIN pg_depend d ON d.refobjid = pv.oid AND d.classid = 'pg_rewrite'::regclass AND d.refclassid = 'pg_class'::regclass
    JOIN pg_rewrite w ON w.oid = d.objid WHERE w.ev_class <> pv.oid),
inv AS (  -- functions and procedures that run with the caller's rights, in the languages a vetted function body can call
  SELECT p.oid, p.oid::regprocedure::text AS name, kernel.src_text(p.prosrc) AS src, p.prosrc, COALESCE(p.proacl, acldefault('f', p.proowner)) AS acl
  FROM pg_proc p JOIN nsp n ON n.oid = p.pronamespace WHERE NOT p.prosecdef AND p.prokind IN ('f', 'p')
    AND p.prolang IN (SELECT oid FROM pg_language WHERE lanname IN ('sql', 'plpgsql'))
  UNION  -- trigger functions fire without an EXECUTE check, so a revoke does not hide them: treat each as callable by PUBLIC, whoever owns it
  SELECT p.oid, p.oid::regprocedure::text, kernel.src_text(p.prosrc), p.prosrc, acldefault('f', p.proowner)
  FROM pg_trigger t JOIN pg_proc p ON p.oid = t.tgfoid JOIN pg_class c ON c.oid = t.tgrelid JOIN nsp n ON n.oid = c.relnamespace
  WHERE NOT t.tgisinternal AND NOT p.prosecdef AND p.prolang IN (SELECT oid FROM pg_language WHERE lanname IN ('sql', 'plpgsql'))),
fn AS (
  SELECT p.oid, p.oid::regprocedure::text AS name, p.proname, p.proowner, p.prosrc, kernel.src_text(p.prosrc) AS src, kernel.src_text(p.prosrc, true) AS srck, n.nspname, g.class, g.scope, g.person_effect, g.dedupe_rel, p.proconfig,
         p.pronargs, p.proretset, p.prorettype, p.proargnames, p.proargmodes, COALESCE(p.proacl, acldefault('f', p.proowner)) AS acl
  FROM pg_proc p JOIN nsp n ON n.oid = p.pronamespace LEFT JOIN kernel.function_registry g ON g.proc = p.oid WHERE p.prosecdef),
chk AS (  -- every body the checks read: all definers, and the invoker routines PUBLIC or a vetted role can run (they run inside a definer's call)
  SELECT name, src, prosrc FROM fn
  UNION ALL SELECT i.name, i.src, i.prosrc FROM inv i
    WHERE EXISTS (SELECT 1 FROM aclexplode(i.acl) x WHERE x.privilege_type = 'EXECUTE' AND (x.grantee = 0 OR x.grantee IN (SELECT oid FROM vetted_roles)))),
can_exec AS (
  SELECT fn.oid AS fn, r.oid AS role FROM fn, (SELECT oid FROM actors UNION SELECT r2.oid FROM pg_roles r2, s WHERE r2.rolname IN (s.backup, s.restore)) r
  WHERE EXISTS (SELECT 1 FROM aclexplode(fn.acl) x WHERE x.privilege_type = 'EXECUTE' AND x.grantee <> 0
                AND x.grantee IN (SELECT role FROM reach WHERE actor = r.oid))),
cat AS (
-- ---- person-level class: global tables keyed by a person, reached by vetted person functions only ----
SELECT '76 person table has an org_id column' AS rule, glob.oid::regclass::text AS object FROM glob
  WHERE glob.person AND EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = glob.oid AND a.attname = 'org_id' AND NOT a.attisdropped)
UNION ALL
SELECT '77 person function is not global scope', fn.name FROM fn WHERE fn.person_effect IS NOT NULL AND fn.scope IS DISTINCT FROM 'global'
UNION ALL -- the acting principal comes from the signed context
SELECT '78 person function does not read app.user_id', fn.name FROM fn
  WHERE fn.person_effect IN ('read', 'self', 'admin') AND fn.srck !~ 'app\.user_id'
UNION ALL
SELECT '79 person function does not resolve the acting principal', fn.name FROM fn
  WHERE (fn.person_effect IN ('read', 'self') AND fn.src !~ 'v_actor') OR (fn.person_effect = 'admin' AND fn.src !~ 'v_admin')
UNION ALL -- (d) every person write takes the per-email lock first
SELECT '80 person write does not take the per-email advisory lock', fn.name FROM fn
  WHERE fn.person_effect IN ('self', 'admin', 'preorg')
    AND (fn.src !~* ('pg_advisory_xact_lock\s*\(\s*7350\s*,\s*hashtext\s*\(\s*(' || COALESCE(array_to_string(fn.proargnames, '|'), '-') || ')\s*\)\s*\)')
         OR fn.src ~* '\m(insert\s+into|update|delete\s+from)\M[\s\S]*pg_advisory_xact_lock')
UNION ALL -- membership proof
SELECT '81 person write has no membership proof', fn.name FROM fn WHERE fn.person_effect IN ('self', 'admin') AND fn.src !~ 'v_member'
UNION ALL -- admin effects are audited and come from a closed list
SELECT '82 admin effect writes no audit row', fn.name FROM fn
  WHERE fn.person_effect = 'admin' AND fn.src !~* 'insert\s+into\s+\S+\s*\(\s*org_id,\s*actor_id,\s*target_email,\s*effect\s*\)'
UNION ALL
SELECT '83 admin effect is not in the closed list', fn.name FROM fn
  WHERE fn.person_effect = 'admin' AND fn.srck !~ '''(unblock|role_change|admin_signout|clear_if_last)'''
UNION ALL -- (e) the pre-org sub-class: exact email, nothing back except consume_otp, never with an org set
SELECT '84 pre-org person function returns a value', fn.name FROM fn
  WHERE fn.person_effect = 'preorg' AND fn.prorettype <> 'void'::regtype AND fn.proname <> 'consume_otp'
UNION ALL
SELECT '84 pre-org person function runs with an org set', fn.name FROM fn WHERE fn.person_effect = 'preorg' AND fn.src !~ 'v_org IS NOT NULL'
UNION ALL -- (b) no caller-chosen thresholds, limits or paging
SELECT '85 person function takes a caller-chosen limit ' || a.name, fn.name FROM fn, LATERAL unnest(fn.proargnames) WITH ORDINALITY a(name, i)
  WHERE fn.person_effect IS NOT NULL AND COALESCE(fn.proargmodes[a.i], 'i') IN ('i', 'b', 'v')
    AND a.name ~* '(^|_)(limit|threshold|cap|max|min|count|size|top|offset|page)($|_)'
UNION ALL
SELECT '85 person read function has a LIMIT that is not a constant', fn.name FROM fn
  WHERE fn.person_effect = 'read' AND fn.proretset
    AND (SELECT count(*) FROM regexp_matches(fn.src, '\mlimit\M', 'gi')) <> (SELECT count(*) FROM regexp_matches(fn.src, '\mlimit\s+\d+\s*(;|\)|offset\M)', 'gi'))
-- ---- jobs-only import and sweep functions (check 31 by owner and by behavior) ----
UNION ALL
SELECT '31 jobs role can execute a ' || COALESCE(fn.class, 'unregistered') || '-class function', fn.name FROM fn JOIN can_exec x ON x.fn = fn.oid, s
  WHERE x.role = to_regrole(s.jobs) AND x.role <> fn.proowner AND fn.class IS DISTINCT FROM 'jobs' AND fn.class IS DISTINCT FROM 'prologue'
UNION ALL
SELECT '31 function of a global table''s vetted owner writes that table and is neither jobs nor write class', fn.name FROM fn, glob, s
  WHERE fn.proowner = to_regrole(s.vetted_prefix || glob.nspname) AND fn.class IS DISTINCT FROM 'jobs' AND fn.class IS DISTINCT FROM 'write'
    AND fn.src ~* ('\m(insert\s+into|update|delete\s+from)\s+(' || glob.nspname || '\.)?' || glob.relname || '\M')
UNION ALL
SELECT '31 jobs-class function that writes a person table or deletes is not named import_* (writes) or sweep_* (deletes)', fn.name FROM fn
  WHERE fn.class = 'jobs'
    AND (SELECT bool_or(fn.src ~* ('\m(insert into|update)\s+(' || g.nspname || '\.)?' || g.relname || '\M')) FROM glob g WHERE g.person) IS NOT DISTINCT FROM true
    AND NOT (fn.proname ~ '^import_' AND fn.src !~* '\m(delete from|truncate)\M')
UNION ALL
SELECT '31 jobs-class function that deletes is not a sweep_* function', fn.name FROM fn
  WHERE fn.class = 'jobs' AND fn.src ~* '\m(delete from|truncate)\M' AND fn.proname !~ '^sweep_'
UNION ALL
SELECT '31 sweep function writes a person table', fn.name FROM fn
  WHERE fn.class = 'jobs' AND fn.proname ~ '^sweep_'
    AND (SELECT bool_or(fn.src ~* ('\m(insert into|update)\s+(' || g.nspname || '\.)?' || g.relname || '\M')) FROM glob g WHERE g.person) IS NOT DISTINCT FROM true
UNION ALL
SELECT '31 import function does not refuse unless a job is running', fn.name FROM fn
  WHERE fn.class = 'jobs' AND fn.proname ~ '^import_' AND fn.src !~* 'platform\.jobs'
UNION ALL
SELECT '31 import function deletes', fn.name FROM fn WHERE fn.class = 'jobs' AND fn.proname ~ '^import_' AND fn.src ~* '\m(delete\s+from|truncate)\M'
UNION ALL
SELECT '31 sweep function takes arguments or has no fixed expiry predicate', fn.name FROM fn
  WHERE fn.class = 'jobs' AND fn.proname ~ '^sweep_'
    AND (fn.pronargs > 0 OR fn.src ~* '\mdelete\s+from\M[\s\S]*\mdelete\s+from\M'
         OR fn.src !~* '\mdelete\s+from\s+[a-z_][a-z0-9_.]*\s+where\s+[a-z_][a-z0-9_.]*\s*<=?\s*(now\s*\(\s*\)|current_timestamp)(\s*-\s*interval\s+'''')?\s*;'
         OR (SELECT count(*) FROM regexp_matches(fn.src, '\minterval\M', 'gi')) <> (SELECT count(*) FROM regexp_matches(fn.srck, '\minterval\s+''[0-9]+\s+(second|minute|hour|day|week|month|year)s?''', 'gi'))
         OR fn.src ~* '\m(insert\s+into|update|merge)\M')
UNION ALL -- vetted DELETE is the sweep functions' alone: no other function deletes, no table opts in without the registry flag
SELECT '88 read or write function deletes', fn.name FROM fn WHERE fn.class IN ('read', 'write') AND fn.src ~* '\m(delete\s+from|truncate)\M'
UNION ALL
SELECT '89 vetted role ' || a.grantee::regrole::text || ' holds ' || a.privilege_type || ' on a table without vetted_delete', c.oid::regclass::text
  FROM pg_class c JOIN nsp n ON n.oid = c.relnamespace JOIN kernel.rls_registry r ON r.rel = c.oid, LATERAL aclexplode(c.relacl) a
  WHERE a.grantee IN (SELECT oid FROM vetted_roles) AND a.privilege_type IN ('DELETE', 'TRUNCATE') AND NOT (r.vetted_delete AND r.class = 'global')
-- ---- the external-issuer dedupe exception ----
UNION ALL
SELECT '90 dedupe table is not an external-issuer exemption', fn.name FROM fn
  WHERE fn.dedupe_rel IS NOT NULL AND NOT EXISTS (SELECT 1 FROM kernel.rls_exemptions e WHERE e.rel = fn.dedupe_rel AND e.value_source = 'external_issuer')
UNION ALL
SELECT '90 dedupe table is not a global table without org data', fn.name FROM fn
  WHERE fn.dedupe_rel IS NOT NULL AND (NOT EXISTS (SELECT 1 FROM kernel.rls_registry g WHERE g.rel = fn.dedupe_rel AND g.class = 'global')
    OR EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = fn.dedupe_rel AND a.attname = 'org_id' AND NOT a.attisdropped))
UNION ALL
SELECT '90 dedupe function returns more than the answer', fn.name FROM fn WHERE fn.dedupe_rel IS NOT NULL AND (fn.prorettype <> 'boolean'::regtype OR fn.proretset)
UNION ALL
SELECT '90 dedupe function writes a table other than its own', fn.name FROM fn,
  LATERAL regexp_matches(fn.src, '\m(?:insert\s+into|update|delete\s+from)\s+([a-z_][a-z0-9_.]*)', 'gi') AS m(g)
  WHERE fn.dedupe_rel IS NOT NULL AND m.g[1] !~* ('^(' || fn.nspname || '\.)?' || (SELECT relname FROM pg_class WHERE oid = fn.dedupe_rel) || '$')
UNION ALL
SELECT '90 dedupe function updates on conflict', fn.name FROM fn WHERE fn.dedupe_rel IS NOT NULL AND fn.src ~* 'do\s+update'
-- ---- the fence (32): barrier, SECURITY DEFINER, and no direct reads for the application ----
UNION ALL
SELECT '32 prologue function is not SECURITY DEFINER', p.oid::regprocedure::text FROM pg_proc p JOIN kernel.function_registry g ON g.proc = p.oid
  WHERE g.class = 'prologue' AND NOT p.prosecdef
UNION ALL
SELECT '32 prologue function takes no shared advisory barrier', p.oid::regprocedure::text FROM pg_proc p JOIN kernel.function_registry g ON g.proc = p.oid
  WHERE g.class = 'prologue' AND kernel.src_text(p.prosrc) !~* 'pg_try_advisory_xact_lock_shared\s*\(\s*\d+\s*,\s*\d+\s*\)'
UNION ALL
SELECT '32 prologue function does not refuse with 25006', p.oid::regprocedure::text FROM pg_proc p JOIN kernel.function_registry g ON g.proc = p.oid
  WHERE g.class = 'prologue' AND kernel.src_text(p.prosrc, true) !~ '25006'
UNION ALL
SELECT '32 application role can read a table the fence reads', v.rel::text FROM kernel.vetted_extension_reads v, pg_class c, LATERAL aclexplode(c.relacl) a
  WHERE c.oid = v.rel AND (a.grantee = 0 OR a.grantee IN (SELECT oid FROM reach_roles))
-- ---- only the person functions touch a person table; only the person writes with the proof write other orgs' rows ----
UNION ALL
SELECT '94 function outside the person functions names the person table ' || glob.relname, fn.name FROM fn, glob
  WHERE glob.person AND fn.person_effect IS NULL AND fn.class IS DISTINCT FROM 'jobs'
    AND fn.src ~* ('\m(' || glob.nspname || '\.)?' || glob.relname || '\M')
UNION ALL
SELECT '95 global or jobs function writes the org-scoped table ' || c.relname, fn.name FROM fn, kernel.rls_registry g JOIN pg_class c ON c.oid = g.rel
  WHERE g.class IN ('org', 'lineage') AND (fn.scope = 'global' OR fn.class = 'jobs') AND fn.person_effect IS DISTINCT FROM 'self' AND fn.person_effect IS DISTINCT FROM 'admin'
    AND fn.src ~* ('\m(insert\s+into|update|delete\s+from)\s+([a-z_][a-z0-9_]*\.)?' || c.relname || '\M')
UNION ALL -- 96 job state is written by the migrator and the owner alone: no application role, the jobs role included, holds any privilege on the jobs table
SELECT '96 application role or PUBLIC holds ' || a.privilege_type || ' on the jobs table', c.oid::regclass::text
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = 'platform' AND c.relname = 'jobs', LATERAL aclexplode(c.relacl) a
  WHERE a.grantee = 0 OR a.grantee IN (SELECT oid FROM reach_roles)
UNION ALL -- 97 a vetted role owns definers only (functions and procedures), and nothing a vetted role can call is an invoker routine that writes
SELECT '97 routine owned by a vetted role is not SECURITY DEFINER', p.oid::regprocedure::text FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
  WHERE p.proowner IN (SELECT oid FROM vetted_roles) AND NOT p.prosecdef AND p.prokind IN ('f', 'p')
UNION ALL
SELECT '97 invoker routine that writes can be called by a vetted role or PUBLIC', i.name FROM inv i
  WHERE i.src ~* '\m(insert into|update [a-z_][a-z0-9_.]*( as)?( [a-z_][a-z0-9_]*)? set|delete from|truncate|merge|execute|call|copy)\M'
    AND EXISTS (SELECT 1 FROM aclexplode(i.acl) x WHERE x.privilege_type = 'EXECUTE' AND (x.grantee = 0 OR x.grantee IN (SELECT oid FROM vetted_roles)))
UNION ALL
SELECT '97 invoker routine that can be called by a vetted role or PUBLIC names the person table ' || g.relname, i.name FROM inv i, glob g
  WHERE g.person AND i.src ~* ('\m(' || g.nspname || '\.)?' || g.relname || '\M')
    AND EXISTS (SELECT 1 FROM aclexplode(i.acl) x WHERE x.privilege_type = 'EXECUTE' AND (x.grantee = 0 OR x.grantee IN (SELECT oid FROM vetted_roles)))
UNION ALL
SELECT '97 invoker routine that can be called by a vetted role or PUBLIC names the kernel table ' || c.relname, i.name FROM inv i, pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
  WHERE n.nspname = 'kernel' AND c.relkind IN ('r', 'p', 'v') AND i.src ~* ('\mkernel\.' || c.relname || '\M')
    AND EXISTS (SELECT 1 FROM aclexplode(i.acl) x WHERE x.privilege_type = 'EXECUTE' AND (x.grantee = 0 OR x.grantee IN (SELECT oid FROM vetted_roles)))
UNION ALL -- 99 a SECURITY DEFINER function sets search_path, standard_conforming_strings = on (the caller's setting would change how its text is compiled) and at most a plan mode
SELECT '99 SECURITY DEFINER function sets ' || split_part(c, '=', 1), fn.name FROM fn, LATERAL unnest(fn.proconfig) c WHERE c !~ '^(search_path|plan_cache_mode|standard_conforming_strings)='
UNION ALL
SELECT '99 SECURITY DEFINER function does not set standard_conforming_strings = on', fn.name FROM fn
  WHERE NOT ('standard_conforming_strings=on' = ANY (COALESCE(fn.proconfig, '{}')))
UNION ALL -- 98 deny by default: a body the checks cannot classify is refused (dollar quotes, E'' and U& strings, quoted identifiers, MERGE, ONLY, FETCH, OFFSET, CALL, UPDATE t*), in definers and in the invoker routines they can reach
SELECT '98 function body uses a construct the checks cannot classify: ' || m.g[1], chk.name FROM chk,
  LATERAL regexp_matches(chk.src, '(\m(?:merge|only|fetch|offset|call)\M|"|\mu&|\m(?:update|from|join|into)\s+[a-z_][a-z0-9_.]*\*)', 'gi') AS m(g)
UNION ALL
SELECT '98 function body uses a construct the checks cannot classify: dollar quote', chk.name FROM chk WHERE chk.prosrc ~ '\$([^\x01-\x7f]|[A-Za-z_$])'
UNION ALL
SELECT '98 function body uses a construct the checks cannot classify: E string', chk.name FROM chk WHERE chk.prosrc ~* '(^|[^A-Za-z0-9_''.$])e'''
UNION ALL -- 94 (views) no view, invoker or not, reads a person table; the walk follows views over views
SELECT '94 view over the person table ' || glob.relname, pv.oid::regclass::text FROM person_views pv JOIN glob ON glob.oid = pv.base WHERE glob.person
-- ---- the earlier text checks again on the normalised text (a comment or extra space cannot hide a keyword pair) ----
UNION ALL
SELECT '56 read/write/jobs function builds SQL dynamically', fn.name FROM fn
  WHERE fn.class IN ('read', 'write', 'jobs') AND fn.src ~* '\mexecute\M|\mformat\s*\(|\m(query|cursor|table|schema|database)_to_xml\w*'
UNION ALL
SELECT '57 write function uses ON CONFLICT', fn.name FROM fn WHERE fn.class = 'write' AND fn.src ~* '\mon conflict\M'
UNION ALL
SELECT '36 read-class function body has DML or dynamic SQL', fn.name FROM fn
  WHERE fn.class = 'read' AND fn.src ~* '\m(insert into|delete from|truncate|execute|copy|merge)\M|\mupdate [a-z_][a-z0-9_.]*( as)?( [a-z_][a-z0-9_]*)? set\M'
UNION ALL
SELECT '61 global function matches by pattern, not by exact key', fn.name FROM fn
  WHERE fn.scope = 'global' AND fn.class IN ('read', 'write') AND fn.src ~* '\m(i?like|similar to)\M|\s!?~\*?\s'
-- ---- shared sequences (rule 8) and advisory-lock keys ----
UNION ALL
SELECT '91 text column takes its default from a shared sequence', c.oid::regclass::text || '.' || a.attname
  FROM pg_attrdef d JOIN pg_attribute a ON a.attrelid = d.adrelid AND a.attnum = d.adnum JOIN pg_class c ON c.oid = d.adrelid JOIN nsp n ON n.oid = c.relnamespace
  WHERE a.atttypid IN ('text'::regtype, 'varchar'::regtype, 'bpchar'::regtype) AND NOT a.attisdropped
    AND EXISTS (SELECT 1 FROM pg_depend dp JOIN pg_class sq ON sq.oid = dp.refobjid AND sq.relkind = 'S'
                WHERE dp.classid = 'pg_attrdef'::regclass AND dp.objid = d.oid AND dp.refclassid = 'pg_class'::regclass)
    AND NOT EXISTS (SELECT 1 FROM kernel.sequence_waivers w WHERE w.rel = c.oid AND w.attname = a.attname
                    AND EXISTS (SELECT 1 FROM pg_depend dp WHERE dp.classid = 'pg_attrdef'::regclass AND dp.objid = d.oid AND dp.refobjid = w.seq))
UNION ALL
SELECT '92 waived sequence no longer feeds its column', w.seq::text FROM kernel.sequence_waivers w
  WHERE NOT EXISTS (SELECT 1 FROM pg_attrdef d JOIN pg_attribute a ON a.attrelid = d.adrelid AND a.attnum = d.adnum
                    JOIN pg_depend dp ON dp.classid = 'pg_attrdef'::regclass AND dp.objid = d.oid AND dp.refobjid = w.seq
                    WHERE d.adrelid = w.rel AND a.attname = w.attname)
UNION ALL
SELECT '92 waived sequence feeds another column', w.seq::text FROM kernel.sequence_waivers w
  WHERE EXISTS (SELECT 1 FROM pg_depend dp JOIN pg_attrdef d ON d.oid = dp.objid AND dp.classid = 'pg_attrdef'::regclass
                WHERE dp.refobjid = w.seq AND NOT (d.adrelid = w.rel AND d.adnum = (SELECT attnum FROM pg_attribute WHERE attrelid = w.rel AND attname = w.attname)))
UNION ALL -- 93 advisory locks: transaction-scoped, two integers, the first a registered class
SELECT '93 session-level advisory lock', fn.name FROM fn WHERE fn.src ~* 'pg_(try_)?advisory_(lock|unlock)\w*\s*\('
UNION ALL
SELECT '93 advisory lock key is not (registered class literal, object)', fn.name FROM fn,
  LATERAL regexp_matches(fn.src, 'pg_(?:try_)?advisory_xact_lock(?:_shared)?\s*\(\s*([^,)]{0,12}[,)]?)', 'gi') AS m(g) WHERE m.g[1] !~ '^\d+,'
UNION ALL
SELECT '93 advisory lock class ' || rtrim(m.g[1], ',') || ' is not registered', fn.name FROM fn,
  LATERAL regexp_matches(fn.src, 'pg_(?:try_)?advisory_xact_lock(?:_shared)?\s*\(\s*([^,)]{0,12}[,)]?)', 'gi') AS m(g)
  WHERE m.g[1] ~ '^\d+,' AND NOT EXISTS (SELECT 1 FROM kernel.lock_keys k WHERE k.class_id::text = rtrim(m.g[1], ','))
)
SELECT rule, object FROM cat;
REVOKE ALL ON kernel.person_violations FROM PUBLIC;

-- The public view: core + vetted + person, minus the reviewed waivers: check 42/73 for the fence's read of the profile row
-- (kernel.vetted_extension_reads) and for recorded job gates, check 57 for registered dedupe functions.
DROP VIEW kernel.rls_violations;
CREATE VIEW kernel.rls_violations WITH (security_invoker = true) AS
SELECT c.rule, c.object FROM kernel.rls_violations_core c
  WHERE NOT (c.rule = '42 vetted role pipeshub_vetted_kernel holds SELECT outside its schema' AND c.object IN (SELECT rel::text FROM kernel.vetted_extension_reads))
    AND NOT EXISTS (SELECT 1 FROM kernel.job_gate_grants j WHERE c.rule = '42 vetted role ' || j.owner || ' holds SELECT outside its schema' AND c.object = j.rel)
UNION ALL
SELECT v.rule, v.object FROM kernel.vetted_violations v
  WHERE NOT EXISTS (SELECT 1 FROM kernel.job_gate_grants j WHERE v.rule = '73 vetted role ' || j.owner || ' holds SELECT on a table that did not opt in' AND v.object = j.rel)
    AND NOT (v.rule = '57 write function uses ON CONFLICT' AND v.object IN (SELECT proc::text FROM kernel.function_registry WHERE dedupe_rel IS NOT NULL))
UNION ALL
SELECT pv.rule, pv.object FROM kernel.person_violations pv
  WHERE NOT (pv.rule = '57 write function uses ON CONFLICT' AND pv.object IN (SELECT proc::text FROM kernel.function_registry WHERE dedupe_rel IS NOT NULL));
REVOKE ALL ON kernel.rls_violations FROM PUBLIC;
