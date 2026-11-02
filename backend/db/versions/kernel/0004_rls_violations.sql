-- Catalog assertions: zero rows = pass. Run by the migrate job after `upgrade heads`, by CI and by the doctor command.
-- Privileges are attributed through the FULL role-membership graph (CTE reach): a privilege parked on a helper role
-- counts for every actor that can reach the helper, by inheritance or by SET ROLE.
CREATE VIEW kernel.rls_violations WITH (security_invoker = true) AS
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
         COALESCE(p.proacl, acldefault('f', p.proowner)) AS acl
  FROM pg_proc p JOIN nsp n ON n.oid = p.pronamespace LEFT JOIN kernel.function_registry g ON g.proc = p.oid
  WHERE p.prosecdef),                                   -- extension members are NOT exempt: anything added to an extension is still checked
-- who can run a function: any role (not only ours) with an ACL entry for it or for a role it can reach. PUBLIC is reported separately.
can_exec AS (
  SELECT fn.oid AS fn, r.oid AS role FROM fn, (SELECT oid FROM actors UNION SELECT r2.oid FROM pg_roles r2, s WHERE r2.rolname IN (s.backup, s.restore)) r
  WHERE EXISTS (SELECT 1 FROM aclexplode(fn.acl) x WHERE x.privilege_type = 'EXECUTE' AND x.grantee <> 0
                AND x.grantee IN (SELECT role FROM reach WHERE actor = r.oid))),
-- 1
cat AS (
SELECT '01 unregistered relation' AS rule, rel::text AS object FROM rels
  WHERE root IS NULL AND oid NOT IN (SELECT objid FROM ext_objs)
UNION ALL
SELECT '01 internal relation has an org_id column', rels.rel::text FROM rels JOIN kernel.rls_registry g ON g.rel = rels.root AND g.class = 'internal'
  WHERE EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = rels.oid AND a.attname = 'org_id' AND NOT a.attisdropped)
UNION ALL -- 2 (every relation in every registered tree, partitions included)
SELECT '02 RLS not enabled+forced', rel::text FROM reg WHERE NOT (relrowsecurity AND relforcerowsecurity)
UNION ALL -- 3
SELECT '03 missing expected policy ' || e.name, e.oid::regclass::text FROM expected e
  WHERE NOT EXISTS (SELECT 1 FROM actual a WHERE a.oid = e.oid AND a.name = e.name)
UNION ALL -- 4 (a stray permissive policy cancels every other one)
SELECT '04 unexpected policy ' || a.name, a.oid::regclass::text FROM actual a JOIN rels r ON r.oid = a.oid
  WHERE NOT EXISTS (SELECT 1 FROM expected e WHERE e.oid = a.oid AND e.name = a.name)
UNION ALL -- 5 (edited expression, command, role or a RESTRICTIVE/permissive flip)
SELECT '05 policy differs from template ' || a.name, a.oid::regclass::text FROM actual a JOIN expected e ON e.oid = a.oid AND e.name = a.name
  WHERE a.cmd <> e.cmd OR NOT a.polpermissive OR a.qual IS DISTINCT FROM e.qual OR a.with_check IS DISTINCT FROM e.with_check
     OR a.roles IS DISTINCT FROM ARRAY[e.role]::name[]
UNION ALL -- 6
SELECT '06 missing org_id CHECK (not empty + format)', rel::text FROM reg WHERE rel = root AND
  (SELECT count(*) FROM pg_constraint k WHERE k.conrelid = reg.oid AND k.conname IN ('org_id_not_empty', 'org_id_format')) < 2
UNION ALL
SELECT '06 org_id is not text NOT NULL', rel::text FROM reg WHERE rel = root AND NOT EXISTS (
  SELECT 1 FROM pg_attribute a WHERE a.attrelid = reg.oid AND a.attname = 'org_id' AND a.atttypid = 'text'::regtype AND a.attnotnull AND NOT a.attisdropped)
UNION ALL -- 7 (unique/PK index without org_id as the first key column)
SELECT '07 unique/PK/EXCLUDE index not led by org_id', i.indexrelid::regclass::text FROM reg JOIN pg_index i ON i.indrelid = reg.oid
  WHERE (i.indisunique OR i.indisexclusion) AND i.indkey[0] <> (SELECT attnum FROM pg_attribute WHERE attrelid = reg.oid AND attname = 'org_id')
    AND NOT EXISTS (SELECT 1 FROM exempt e WHERE e.conindid = i.indexrelid AND e.cur_md5 = e.condef_md5)
UNION ALL -- 7b (exemptions are pinned and need a server-side value source)
SELECT '07 exempted constraint missing or definition changed', e.rel::text || '.' || e.conname FROM exempt e
  WHERE e.conoid IS NULL OR e.cur_md5 <> e.condef_md5
UNION ALL
SELECT '07 exempted key has no server-generated value', e.rel::text || '.' || e.conname FROM exempt e
  JOIN pg_constraint c ON c.oid = e.conoid
  WHERE e.value_source IN ('sequence', 'random') AND EXISTS (SELECT 1 FROM unnest(c.conkey) k WHERE NOT EXISTS (
    SELECT 1 FROM pg_attribute a LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
    WHERE a.attrelid = c.conrelid AND a.attnum = k AND (a.attidentity <> ''
      OR (e.value_source = 'sequence' AND pg_get_expr(d.adbin, d.adrelid) ~ 'nextval\(')
      OR (e.value_source = 'random' AND pg_get_expr(d.adbin, d.adrelid) ~ '(gen_random_uuid|gen_random_bytes|random)\('))))
UNION ALL -- 8 (FK must map org_id to org_id first; one row per FK, not per partition clone)
SELECT '08 FK not led by org_id', k.conname || ' on ' || k.conrelid::regclass::text FROM pg_constraint k
  WHERE k.contype = 'f' AND k.conparentid = 0 AND (k.conrelid IN (SELECT oid FROM reg) OR k.confrelid IN (SELECT oid FROM reg))
    AND ((SELECT attname FROM pg_attribute WHERE attrelid = k.conrelid AND attnum = k.conkey[1]) IS DISTINCT FROM 'org_id'
      OR (SELECT attname FROM pg_attribute WHERE attrelid = k.confrelid AND attnum = k.confkey[1]) IS DISTINCT FROM 'org_id')
UNION ALL -- 8b (a FK into an org-scoped table from an unregistered table is an RI existence oracle)
SELECT '08 FK into an org-scoped table from an unregistered table', k.conname || ' on ' || k.conrelid::regclass::text FROM pg_constraint k
  WHERE k.contype = 'f' AND k.conparentid = 0 AND k.confrelid IN (SELECT oid FROM reg) AND k.conrelid NOT IN (SELECT oid FROM reg)
UNION ALL -- 9 (every relation, in every schema, is owned by the owner role)
SELECT '09 relation not owned by the owner role', c.oid::regclass::text FROM pg_class c JOIN nsp n ON n.oid = c.relnamespace, s
  WHERE c.relkind IN ('r', 'p', 'v', 'm', 'S', 'f') AND c.oid NOT IN (SELECT objid FROM ext_objs)
    AND c.relowner <> (SELECT oid FROM pg_roles WHERE rolname = s.owner)
UNION ALL
SELECT '09 schema not owned by the owner role', n.nspname FROM nsp n JOIN pg_namespace x ON x.oid = n.oid, s
  WHERE n.nspname <> 'public' AND x.nspowner <> (SELECT oid FROM pg_roles WHERE rolname = s.owner)
UNION ALL -- 10 (partitions: only the owner may hold privileges)
SELECT '10 partition has grants', rels.rel::text FROM rels, s, LATERAL aclexplode(rels.relacl) a
  WHERE rels.relispartition AND a.grantee <> (SELECT oid FROM pg_roles WHERE rolname = s.owner)
    AND NOT (a.grantee = to_regrole(s.backup) AND a.privilege_type = 'SELECT')
    AND NOT (a.grantee = to_regrole(s.restore) AND a.privilege_type = 'INSERT')
UNION ALL -- 11 (grants go to one writer role, never to the policy group)
SELECT '11 table privilege granted to the app group', rels.rel::text FROM rels, LATERAL aclexplode(rels.relacl) a
  WHERE a.grantee = (SELECT oid FROM grp)
UNION ALL -- 12 (a privilege held by any role an actor can reach counts for the actor)
SELECT '12 app actor can ' || a.privilege_type, rels.rel::text FROM rels, LATERAL aclexplode(rels.relacl) a
  WHERE a.privilege_type IN ('TRUNCATE', 'REFERENCES', 'TRIGGER', 'MAINTAIN') AND (a.grantee = 0 OR a.grantee IN (SELECT oid FROM reach_roles))
UNION ALL -- 13 (CREATE on schemas / database; TEMP for PUBLIC)
SELECT '13 CREATE on schema for app actor/PUBLIC', n.nspname FROM nsp n JOIN pg_namespace x ON x.oid = n.oid, LATERAL aclexplode(COALESCE(x.nspacl, acldefault('n', x.nspowner))) a
  WHERE a.privilege_type = 'CREATE' AND (a.grantee = 0 OR a.grantee IN (SELECT oid FROM reach_roles))
UNION ALL
SELECT '13 ' || a.privilege_type || ' on database for app actor/PUBLIC', d.datname FROM pg_database d, LATERAL aclexplode(COALESCE(d.datacl, acldefault('d', d.datdba))) a
  WHERE d.datname = current_database() AND ((a.privilege_type = 'CREATE' AND (a.grantee = 0 OR a.grantee IN (SELECT oid FROM reach_roles)))
                                         OR (a.privilege_type = 'TEMPORARY' AND a.grantee = 0))
UNION ALL -- 14 (views anywhere, the kernel schema included, unless they belong to an extension)
SELECT '14 view not security_invoker', c.oid::regclass::text FROM pg_class c JOIN nsp n ON n.oid = c.relnamespace
  WHERE c.relkind = 'v'
    AND NOT EXISTS (SELECT 1 FROM unnest(c.reloptions) o WHERE o ~* '^security_invoker=(true|on|yes|1)$')
UNION ALL
SELECT '14 materialized view', c.oid::regclass::text FROM pg_class c JOIN nsp n ON n.oid = c.relnamespace
  WHERE c.relkind = 'm'
UNION ALL -- 15 (the kernel schema included)
SELECT '15 SECURITY DEFINER not owned by its schema''s vetted role', fn.name FROM fn, s
  WHERE fn.proowner IS DISTINCT FROM (SELECT oid FROM pg_roles WHERE rolname = s.vetted_prefix || fn.nspname)
UNION ALL -- 16 (pg_catalog first, exactly one pg_temp and it is last: the first pg_temp wins)
SELECT '16 SECURITY DEFINER search_path not pg_catalog,…,pg_temp', fn.name FROM fn
  WHERE NOT EXISTS (SELECT 1 FROM pg_proc p, unnest(p.proconfig) cfg WHERE p.oid = fn.oid
    AND cfg ~ '^search_path=pg_catalog, ' AND cfg ~ ', pg_temp$' AND cfg !~ 'pg_temp.*pg_temp' AND cfg !~ '"')
UNION ALL -- 17
SELECT '17 SECURITY DEFINER executable by PUBLIC', fn.name FROM fn
  WHERE EXISTS (SELECT 1 FROM aclexplode(fn.acl) x WHERE x.grantee = 0 AND x.privilege_type = 'EXECUTE')
UNION ALL -- 18 (nobody becomes a vetted owner; only the migrator becomes the owner)
SELECT '18 member of vetted role ' || v.rolname, m.member::regrole::text FROM vetted_roles v JOIN pg_auth_members m ON m.roleid = v.oid, s
  WHERE NOT (m.member = to_regrole(s.owner) AND m.admin_option AND NOT m.inherit_option AND NOT m.set_option)   -- ADMIN only: may grant, cannot act
UNION ALL
SELECT '18 member of owner role', m.member::regrole::text FROM pg_auth_members m, s
  WHERE m.roleid = (SELECT oid FROM pg_roles WHERE rolname = s.owner) AND m.member <> (SELECT oid FROM pg_roles WHERE rolname = s.migrate)
UNION ALL -- 19 (nobody can SET ROLE into the policy group)
SELECT '19 can SET ROLE to the app group', m.member::regrole::text FROM pg_auth_members m, grp WHERE m.roleid = grp.oid AND m.set_option
UNION ALL -- 20 (a login reaches at most one service role, by any path)
SELECT '20 login is a member of several service roles', r.actor::regrole::text FROM reach r JOIN svc ON svc.oid = r.role
  WHERE r.actor IN (SELECT oid FROM logins) GROUP BY r.actor HAVING count(DISTINCT r.role) > 1
UNION ALL -- 25 (a login holds service/group privileges without the unit of work's SET ROLE)
SELECT '25 login inherits ' || r.role::regrole::text || ' (needs INHERIT FALSE)', r.actor::regrole::text FROM reach r, s
  WHERE r.actor IN (SELECT oid FROM logins) AND r.role <> r.actor AND r.all_inherit
    AND (r.role IN (SELECT oid FROM svc) OR (r.role = (SELECT oid FROM grp) AND r.actor IS DISTINCT FROM to_regrole(s.ro)))
UNION ALL -- 21 (ALTER ROLE … SET app.* / role)
SELECT '21 role/database setting ' || cfg, COALESCE(r.rolname, '(all roles)') FROM pg_db_role_setting d LEFT JOIN pg_roles r ON r.oid = d.setrole,
  LATERAL unnest(d.setconfig) cfg WHERE cfg ~* '^(app\.|role=|session_authorization=)'
UNION ALL -- 22 (no role bypasses RLS, whatever its name; the only superuser is the bootstrap one)
SELECT '22 role can bypass RLS', r.rolname FROM pg_roles r, s
  WHERE r.rolbypassrls AND NOT r.rolsuper AND r.rolname !~ '^pg_'
UNION ALL
SELECT '22 superuser other than the bootstrap superuser', r.rolname FROM pg_roles r
  WHERE r.rolsuper AND r.oid <> 10 AND r.oid <> (SELECT datdba FROM pg_database WHERE datname = current_database())
UNION ALL -- 23 (INVALID indexes left by a failed CREATE INDEX CONCURRENTLY)
SELECT '23 invalid index', i.indexrelid::regclass::text FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid JOIN app_nsp n ON n.oid = c.relnamespace
  WHERE NOT i.indisvalid
UNION ALL -- 24 (a registry row whose relation was dropped: the migration must drop the registry row too)
SELECT '24 registry row for a dropped relation', g.rel::oid::text FROM kernel.rls_registry g WHERE NOT EXISTS (SELECT 1 FROM pg_class c WHERE c.oid = g.rel::oid)
UNION ALL -- 26 (global relations are reachable only through vetted functions; privileges parked on a reachable role count)
SELECT '26 app actor/PUBLIC holds ' || a.privilege_type || ' on a global table', glob.rel::text FROM glob, LATERAL aclexplode(glob.relacl) a
  WHERE a.grantee = 0 OR a.grantee IN (SELECT oid FROM reach_roles)
UNION ALL
SELECT '26 restore role holds ' || a.privilege_type || ' on a global table', glob.rel::text FROM glob, s, LATERAL aclexplode(glob.relacl) a
  WHERE a.grantee = to_regrole(s.restore)
UNION ALL -- 27 (backup is read-only and owns nothing)
SELECT '27 backup role holds ' || a.privilege_type, c.oid::regclass::text FROM pg_class c JOIN nsp n ON n.oid = c.relnamespace, s,
  LATERAL aclexplode(c.relacl) a WHERE a.grantee = to_regrole(s.backup) AND a.privilege_type <> 'SELECT'
UNION ALL
SELECT '27 backup role owns or is a member', s.backup FROM s WHERE EXISTS (SELECT 1 FROM pg_class WHERE relowner = to_regrole(s.backup))
  OR EXISTS (SELECT 1 FROM pg_auth_members WHERE member = to_regrole(s.backup))
UNION ALL -- 28 (restore is INSERT-only (plus SELECT of its window row), owns nothing, and logs in only inside an open window)
SELECT '28 restore role holds ' || a.privilege_type, c.oid::regclass::text FROM pg_class c JOIN nsp n ON n.oid = c.relnamespace, s,
  LATERAL aclexplode(c.relacl) a WHERE a.grantee = to_regrole(s.restore) AND a.privilege_type <> 'INSERT'
    AND NOT (c.oid = 'kernel.restore_window'::regclass AND a.privilege_type = 'SELECT')
UNION ALL
SELECT '28 restore role owns or is a member', s.restore FROM s, pg_roles r WHERE r.rolname = s.restore
  AND (EXISTS (SELECT 1 FROM pg_class WHERE relowner = r.oid) OR EXISTS (SELECT 1 FROM pg_auth_members WHERE member = r.oid))
UNION ALL
SELECT '28 restore role can log in without an open window', s.restore FROM s, pg_roles r WHERE r.rolname = s.restore AND r.rolcanlogin
  AND NOT EXISTS (SELECT 1 FROM kernel.restore_window w WHERE w.until > now())
UNION ALL
SELECT '28 restore window expired and not closed', w.org FROM kernel.restore_window w WHERE w.until <= now()
UNION ALL -- 29 (an exempted table is reached only through its vetted functions)
SELECT '29 app actor/PUBLIC holds ' || a.privilege_type || ' on an exempted table', e.rel::text FROM kernel.rls_exemptions e, pg_class c,
  LATERAL aclexplode(c.relacl) a WHERE c.oid = e.rel AND (a.grantee = 0 OR a.grantee IN (SELECT oid FROM reach_roles))
UNION ALL -- 30 (kernel tables, exemption rows included, are pinned: migrator/owner only; two reviewed exceptions)
SELECT '30 grant on kernel table to ' || a.grantee::regrole::text, c.oid::regclass::text FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace, s,
  LATERAL aclexplode(c.relacl) a WHERE n.nspname = 'kernel' AND c.relkind IN ('r', 'p', 'v', 'm') AND a.grantee <> c.relowner
    AND NOT (c.relname = 'schema_epoch' AND a.privilege_type = 'SELECT' AND a.grantee IN (SELECT oid FROM svc))
    AND NOT (c.relname = 'restore_window' AND a.privilege_type = 'SELECT' AND a.grantee = to_regrole(s.restore))
UNION ALL -- 31 (jobs-only functions: import / sweep; classified in the registry, never matched by name alone)
SELECT '31 jobs-only function executable by ' || x.role::regrole::text, fn.name FROM fn JOIN can_exec x ON x.fn = fn.oid, s
  WHERE fn.class = 'jobs' AND x.role <> fn.proowner AND x.role IS DISTINCT FROM to_regrole(s.jobs)
UNION ALL
SELECT '31 import/sweep function is not registered as jobs-only', fn.name FROM fn
  WHERE fn.proname ~ '^(import|sweep)_' AND fn.class IS DISTINCT FROM 'jobs'
UNION ALL -- 32 (the unit-of-work prologue calls these before the role switch: VOLATILE, or the planner drops the call)
SELECT '32 prologue function is not VOLATILE', fn.name FROM fn WHERE fn.class = 'prologue' AND fn.provolatile <> 'v'
UNION ALL
SELECT '32 prologue fence does not assert transaction_isolation', fn.name FROM fn
  WHERE fn.class = 'prologue' AND fn.prosrc !~ 'transaction_isolation'
UNION ALL
SELECT '32 prologue function executable by PUBLIC', fn.name FROM fn
  WHERE fn.class = 'prologue' AND EXISTS (SELECT 1 FROM aclexplode(fn.acl) x WHERE x.grantee = 0 AND x.privilege_type = 'EXECUTE')
UNION ALL
SELECT '32 prologue function executable by ' || x.role::regrole::text, fn.name FROM fn JOIN can_exec x ON x.fn = fn.oid, s
  WHERE fn.class = 'prologue' AND (x.role IN (SELECT oid FROM grp) OR x.role IN (SELECT oid FROM pg_roles WHERE rolname IN (s.backup, s.restore, s.ro)))
UNION ALL -- 33 (a write function is executable by its one writer service role (and that role's logins), by nobody else, PUBLIC included)
SELECT '33 write-class function executable by ' || x.role::regrole::text, fn.name FROM fn JOIN can_exec x ON x.fn = fn.oid
  WHERE fn.class = 'write' AND x.role <> fn.proowner AND x.role IS DISTINCT FROM to_regrole(fn.writer)
    AND NOT EXISTS (SELECT 1 FROM pg_auth_members m WHERE m.member = x.role AND m.roleid = to_regrole(fn.writer))
UNION ALL
SELECT '33 write-class function executable by PUBLIC', fn.name FROM fn
  WHERE fn.class IN ('write', 'jobs') AND EXISTS (SELECT 1 FROM aclexplode(fn.acl) x WHERE x.grantee = 0 AND x.privilege_type = 'EXECUTE')
UNION ALL -- 34
SELECT '34 write function not executable by its writer role', fn.name FROM fn
  WHERE fn.class = 'write' AND NOT EXISTS (SELECT 1 FROM can_exec x WHERE x.fn = fn.oid AND x.role = to_regrole(fn.writer))
UNION ALL -- 35 (every SECURITY DEFINER function is classified, and a read function does not call a write function)
SELECT '35 SECURITY DEFINER function not in kernel.function_registry', fn.name FROM fn WHERE fn.class IS NULL
UNION ALL
SELECT '35 read-class function calls ' || g.name, f.name FROM fn f JOIN fn g ON g.class IN ('write', 'jobs') AND f.prosrc ~* ('\m' || g.proname || '\M')
  WHERE f.class = 'read'
UNION ALL -- 36 (registry consistency)
SELECT '36 read-class function is VOLATILE', fn.name FROM fn WHERE fn.class = 'read' AND fn.provolatile = 'v'
UNION ALL
SELECT '36 read-class function body has DML or dynamic SQL', fn.name FROM fn
  WHERE fn.class = 'read' AND fn.prosrc ~* '\m(insert\s+into|delete\s+from|truncate|execute|copy)\M|\mupdate\s+[a-z_."]+\s+set\M'
UNION ALL
SELECT '36 registry row for a dropped function', g.proc::oid::text FROM kernel.function_registry g WHERE NOT EXISTS (SELECT 1 FROM pg_proc p WHERE p.oid = g.proc::oid)
UNION ALL -- 37 (membership allowlist: exactly the expected memberships, for every role in the cluster; anything else fails)
SELECT '37 unexpected membership ' || m.member::regrole::text || ' -> ' || m.roleid::regrole::text, m.member::regrole::text
  FROM pg_auth_members m, s
  WHERE m.member NOT IN (SELECT oid FROM pg_roles WHERE rolsuper OR rolname LIKE 'pg\_%')
    AND NOT (m.member IN (SELECT oid FROM admin_roles) AND (m.roleid IN (SELECT oid FROM admin_roles) OR m.roleid IN (SELECT oid FROM pg_roles WHERE rolname LIKE 'pg\_%')))
    AND NOT ((m.member IN (SELECT oid FROM logins) AND m.roleid IN (SELECT oid FROM svc) AND NOT m.admin_option
              AND m.member = to_regrole((SELECT rolname FROM pg_roles WHERE oid = m.roleid) || '_login')              -- each service has exactly one login
              AND m.member NOT IN (SELECT oid FROM pg_roles WHERE rolname IN (s.jobs, s.ro, s.migrate, s.backup, s.restore)))
          OR (m.member IN (SELECT oid FROM svc) AND m.roleid = (SELECT oid FROM grp))
          OR (m.member = to_regrole(s.ro) AND m.roleid = (SELECT oid FROM grp))
          OR (m.member = to_regrole(s.migrate) AND m.roleid = to_regrole(s.owner))
          OR (m.member = to_regrole(s.owner) AND m.roleid IN (SELECT oid FROM vetted_roles)
              AND m.admin_option AND NOT m.inherit_option AND NOT m.set_option))
UNION ALL -- 37b (administrators are separate from the application: no edge, direct or transitive, in either direction)
SELECT '37 application role reaches administrator role ' || r.role::regrole::text, r.actor::regrole::text FROM reach r
  WHERE r.actor IN (SELECT oid FROM actors) AND r.role IN (SELECT oid FROM admin_roles)
UNION ALL
SELECT '37 administrator role reaches application role ' || r.role::regrole::text, r.actor::regrole::text FROM reach r
  WHERE r.actor IN (SELECT oid FROM admin_roles) AND r.role <> r.actor AND r.role IN (SELECT oid FROM actors UNION SELECT oid FROM trusted)
UNION ALL
SELECT '37 settings.admins lists an application role', r.rolname FROM pg_roles r, s
  WHERE r.oid IN (SELECT oid FROM admin_roles) AND (r.rolname LIKE s.role_prefix || '%' OR r.oid IN (SELECT oid FROM trusted UNION SELECT oid FROM svc UNION SELECT oid FROM grp)
    OR r.rolname IN (s.jobs, s.ro))
UNION ALL -- 38 (column-level privileges are invisible to relacl checks and the kernel grants none)
SELECT '38 column-level privilege on ' || a.attname, c.oid::regclass::text FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid
  JOIN nsp n ON n.oid = c.relnamespace, LATERAL aclexplode(a.attacl) x
  WHERE a.attacl IS NOT NULL AND NOT a.attisdropped AND x.grantee <> c.relowner
UNION ALL -- 39 (roles that own objects or run SECURITY DEFINER code never log in)
SELECT '39 role that owns objects or must be NOLOGIN can log in', r.rolname FROM pg_roles r, s
  WHERE r.rolcanlogin AND NOT r.rolsuper AND (r.rolname IN (s.owner, s.app_group) OR r.oid IN (SELECT oid FROM svc) OR r.oid IN (SELECT oid FROM vetted_roles)
    OR r.oid IN (SELECT c.relowner FROM pg_class c JOIN nsp n ON n.oid = c.relnamespace WHERE c.relkind IN ('r', 'p', 'v', 'm', 'S', 'f') AND c.oid NOT IN (SELECT objid FROM ext_objs))
    OR r.oid IN (SELECT p.proowner FROM pg_proc p JOIN nsp n ON n.oid = p.pronamespace WHERE p.prosecdef)
    OR r.oid IN (SELECT n.nspowner FROM nsp x JOIN pg_namespace n ON n.oid = x.oid))
UNION ALL -- 40 (a rule runs with the table owner's rights)
SELECT '40 rewrite rule ' || w.rulename, c.oid::regclass::text FROM pg_rewrite w JOIN pg_class c ON c.oid = w.ev_class JOIN nsp n ON n.oid = c.relnamespace
  WHERE w.rulename <> '_RETURN' AND c.oid NOT IN (SELECT objid FROM ext_objs)
UNION ALL -- 41 (role attributes that reach around RLS or the role design)
SELECT '41 role holds ' || x.attr, r.rolname FROM pg_roles r, LATERAL (VALUES ('REPLICATION', r.rolreplication), ('CREATEROLE', r.rolcreaterole),
  ('CREATEDB', r.rolcreatedb)) x(attr, held) WHERE x.held AND r.oid IN (SELECT oid FROM rall) AND r.oid NOT IN (SELECT oid FROM admin_roles)
UNION ALL -- 42 (a vetted role holds privileges in its own schema only)
SELECT '42 vetted role ' || a.grantee::regrole::text || ' holds ' || a.privilege_type || ' outside its schema', c.oid::regclass::text
  FROM pg_class c JOIN nsp n ON n.oid = c.relnamespace, s, LATERAL aclexplode(c.relacl) a
  WHERE a.grantee IN (SELECT oid FROM vetted_roles) AND a.grantee <> COALESCE(to_regrole(s.vetted_prefix || n.nspname), 0::oid::regrole)
UNION ALL -- 43 (large objects have no owner-org: nobody may create or read them)
SELECT '43 large objects exist', count(*)::text FROM pg_largeobject_metadata HAVING count(*) > 0
UNION ALL
SELECT '43 role can use large object function ' || f.sig, a.oid::regrole::text
  FROM actors a, unnest(ARRAY['lo_create(oid)', 'lo_creat(integer)', 'lo_from_bytea(oid,bytea)', 'lo_put(oid,bigint,bytea)', 'lo_get(oid)',
    'lo_get(oid,bigint,integer)', 'lo_unlink(oid)', 'lo_open(oid,integer)', 'loread(integer,integer)', 'lowrite(integer,bytea)']) f(sig)
  WHERE to_regprocedure('pg_catalog.' || f.sig) IS NOT NULL AND has_function_privilege(a.oid, to_regprocedure('pg_catalog.' || f.sig), 'EXECUTE')
    AND NOT COALESCE((SELECT (profile->>'managed')::boolean FROM platform.deployment_profile), false)   -- managed: a warning (kernel.rls_warnings)
UNION ALL -- 44 (consumer inbox: replay protection must be per org)
SELECT '44 consumer inbox primary key is not (org_id, message_id)', c.oid::regclass::text FROM pg_class c
  WHERE c.oid = to_regclass('platform.consumer_inbox') AND (SELECT array_agg(a.attname ORDER BY k.ord) FROM pg_constraint pc
    CROSS JOIN LATERAL unnest(pc.conkey) WITH ORDINALITY k(attnum, ord) JOIN pg_attribute a ON a.attrelid = pc.conrelid AND a.attnum = k.attnum
    WHERE pc.conrelid = c.oid AND pc.contype = 'p') IS DISTINCT FROM ARRAY['org_id', 'message_id']::name[]
UNION ALL -- 45 (no foreign data: a foreign table or user mapping reads another database with someone else's credentials)
SELECT '45 foreign table', c.oid::regclass::text FROM pg_class c JOIN nsp n ON n.oid = c.relnamespace WHERE c.relkind = 'f'
UNION ALL
SELECT '45 foreign server', srvname FROM pg_foreign_server
UNION ALL
SELECT '45 user mapping', um.srvname || ' for ' || COALESCE(um.usename, 'PUBLIC') FROM pg_user_mappings um
UNION ALL -- 46 (UPDATE on a sequence is setval: one role can push every org's keys into a collision)
SELECT '46 app actor/PUBLIC can setval a sequence', c.oid::regclass::text FROM pg_class c JOIN nsp n ON n.oid = c.relnamespace,
  LATERAL aclexplode(c.relacl) a WHERE c.relkind = 'S' AND a.privilege_type = 'UPDATE' AND (a.grantee = 0 OR a.grantee IN (SELECT oid FROM reach_roles))
UNION ALL -- 47 (default privileges grant to objects that do not exist yet)
SELECT '47 default privileges', COALESCE(d.defaclrole::regrole::text, '') || ' ' || d.defaclobjtype::text FROM pg_default_acl d
UNION ALL -- 48 (no logical replication except the outbox publication, no event triggers)
SELECT '48 event trigger', evtname FROM pg_event_trigger
UNION ALL
SELECT '48 subscription', subname FROM pg_subscription
UNION ALL
SELECT '48 publication', p.pubname FROM pg_publication p
  WHERE NOT (p.pubname = 'pipeshub_outbox' AND NOT p.puballtables AND NOT EXISTS (
    SELECT 1 FROM pg_publication_rel r WHERE r.prpubid = p.oid AND r.prrelid <> COALESCE(to_regclass('platform.outbox'), 0::oid::regclass)))
UNION ALL -- 49 (LEAKPROOF lets a function run before the RLS qual)
SELECT '49 LEAKPROOF function', p.oid::regprocedure::text FROM pg_proc p JOIN nsp n ON n.oid = p.pronamespace
  WHERE p.proleakproof AND p.oid NOT IN (SELECT objid FROM ext_objs)
UNION ALL -- 51 (only the allowlisted extensions are installed)
SELECT '51 extension not allowed', e.extname FROM pg_extension e, s WHERE NOT e.extname = ANY (s.extensions)
UNION ALL -- 32b (registered prologue functions that are not SECURITY DEFINER: same rules)
SELECT '32 prologue function is not VOLATILE', p.oid::regprocedure::text FROM pg_proc p JOIN kernel.function_registry g ON g.proc = p.oid
  WHERE g.class = 'prologue' AND NOT p.prosecdef AND p.provolatile <> 'v'
UNION ALL
SELECT '32 prologue fence does not assert transaction_isolation', p.oid::regprocedure::text FROM pg_proc p JOIN kernel.function_registry g ON g.proc = p.oid
  WHERE g.class = 'prologue' AND NOT p.prosecdef AND p.prosrc !~ 'transaction_isolation'
UNION ALL
SELECT '32 prologue function executable by PUBLIC', p.oid::regprocedure::text FROM pg_proc p JOIN kernel.function_registry g ON g.proc = p.oid
  WHERE g.class = 'prologue' AND NOT p.prosecdef
    AND EXISTS (SELECT 1 FROM aclexplode(COALESCE(p.proacl, acldefault('f', p.proowner))) x WHERE x.grantee = 0 AND x.privilege_type = 'EXECUTE')
UNION ALL -- 52 (nothing user-made lives in the system schemas)
SELECT '52 user-created ' || x.kind || ' in a system schema', x.name FROM (
  SELECT 'relation' AS kind, c.oid::regclass::text AS name FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname IN ('pg_catalog', 'information_schema') AND c.oid >= 16384
  UNION ALL SELECT 'function', p.oid::regprocedure::text FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname IN ('pg_catalog', 'information_schema') AND p.oid >= 16384
  UNION ALL SELECT 'type', t.oid::regtype::text FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace
    WHERE n.nspname IN ('pg_catalog', 'information_schema') AND t.oid >= 16384) x
UNION ALL -- 53 (an app actor or PUBLIC holds a privilege on a system relation or function that initdb did not grant)
SELECT '53 app actor/PUBLIC holds ' || a.privilege_type || ' on system relation', c.oid::regclass::text FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace,
  LATERAL aclexplode(c.relacl) a WHERE n.nspname IN ('pg_catalog', 'information_schema') AND (a.grantee = 0 OR a.grantee IN (SELECT oid FROM reach_roles))
    AND c.relacl IS DISTINCT FROM (SELECT i.initprivs FROM pg_init_privs i WHERE i.objoid = c.oid AND i.classoid = 'pg_class'::regclass AND i.objsubid = 0)
    AND NOT (a.grantee = 0 AND a.privilege_type IN ('SELECT', 'USAGE', 'EXECUTE') AND c.relnamespace = 'information_schema'::regnamespace)
UNION ALL
SELECT '53 app actor/PUBLIC can execute system function ' || p.proname, p.oid::regprocedure::text FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace,
  LATERAL aclexplode(p.proacl) a WHERE n.nspname IN ('pg_catalog', 'information_schema') AND a.privilege_type = 'EXECUTE'
    AND (a.grantee = 0 OR a.grantee IN (SELECT oid FROM reach_roles))
    AND p.proname !~ '^(lo_|lo(read|write)$)'
    AND p.proacl IS DISTINCT FROM (SELECT i.initprivs FROM pg_init_privs i WHERE i.objoid = p.oid AND i.classoid = 'pg_proc'::regclass AND i.objsubid = 0)
UNION ALL -- 54 (parameter and tablespace privileges)
SELECT '54 app actor/PUBLIC holds ' || a.privilege_type || ' on parameter ' || pa.parname, pa.parname FROM pg_parameter_acl pa, LATERAL aclexplode(pa.paracl) a
  WHERE a.grantee = 0 OR a.grantee IN (SELECT oid FROM reach_roles)
UNION ALL
SELECT '54 app actor/PUBLIC holds ' || a.privilege_type || ' on tablespace ' || t.spcname, t.spcname FROM pg_tablespace t,
  LATERAL aclexplode(COALESCE(t.spcacl, acldefault('t', t.spcowner))) a
  WHERE a.grantee <> t.spcowner AND (a.grantee = 0 OR a.grantee IN (SELECT oid FROM reach_roles))
)
SELECT rule, object FROM cat;

-- The post-run check and the doctor command read the view as the migrator.
REVOKE ALL ON kernel.rls_violations FROM PUBLIC;

-- Known exceptions on a declared managed platform: reported, not failed.
CREATE VIEW kernel.rls_warnings WITH (security_invoker = true) AS
SELECT '43 managed platform: large object function executable by PUBLIC' AS rule, p.oid::regprocedure::text AS object
FROM pg_proc p WHERE p.pronamespace = 'pg_catalog'::regnamespace AND p.proname ~ '^(lo_|lo(read|write)$)'
  AND EXISTS (SELECT 1 FROM aclexplode(COALESCE(p.proacl, acldefault('f', p.proowner))) x WHERE x.grantee = 0 AND x.privilege_type = 'EXECUTE')
  AND COALESCE((SELECT (profile->>'managed')::boolean FROM platform.deployment_profile), false);
REVOKE ALL ON kernel.rls_warnings FROM PUBLIC;
