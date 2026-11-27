-- A managed PostgreSQL hands out an administrator that is not a superuser: it can create roles and databases (CREATEROLE, CREATEDB)
-- and owns the database, and the platform's own superuser installs the allow-listed extensions. The checks treat that administrator
-- as they treat the bootstrap superuser:
-- * kernel.bootstrap_admin() names it: the owner of this database when it is not a superuser, holds CREATEROLE, has no BYPASSRLS
--   and is neither one of our roles nor a predefined one. Otherwise NULL (a superuser bootstrap has none).
-- * It is not an application actor (as superusers are not). On PostgreSQL 16 and later it holds ADMIN on every role it created,
--   without INHERIT or SET; checks 18 and 37 accept exactly those memberships of it. With INHERIT or SET on one of our roles it would act
--   with that role's privileges: '37 bootstrap administrator holds an application role'.
-- * Any role that can reach it is still an actor and still holds what it reaches: the administrator gains nothing for the
--   application roles, and an application login that is a member of it fails the checks as before.
-- * Without a superuser the large-object functions (owned by the platform's superuser) cannot be revoked: check 43 is the warning
--   it is on a declared managed platform (kernel.rls_warnings).
CREATE FUNCTION kernel.bootstrap_admin() RETURNS oid
LANGUAGE sql STABLE SET search_path = pg_catalog, kernel, pg_temp AS $$
  SELECT r.oid FROM pg_database d JOIN pg_roles r ON r.oid = d.datdba, kernel.settings s
  WHERE d.datname = current_database() AND NOT r.rolsuper AND r.rolcreaterole AND NOT r.rolbypassrls
    AND r.rolname NOT LIKE s.role_prefix || '%' AND r.rolname NOT LIKE 'pg\_%' $$;
REVOKE ALL ON FUNCTION kernel.bootstrap_admin() FROM PUBLIC;

-- (IS NOT DISTINCT FROM: with no bootstrap administrator the comparison must be false, never NULL, or NOT would drop the row.)
-- kernel_0023's test, with the bootstrap administrator's ADMIN-only grant (PostgreSQL 16+) accepted beside the owner role's
CREATE OR REPLACE FUNCTION kernel._fresh_vetted_owner(p_role oid) RETURNS boolean
LANGUAGE sql STABLE SET search_path = pg_catalog, kernel, pg_temp AS $$
  SELECT NOT r.rolcanlogin AND NOT r.rolsuper AND NOT r.rolcreaterole AND NOT r.rolcreatedb AND NOT r.rolbypassrls AND NOT r.rolreplication
    AND NOT EXISTS (SELECT 1 FROM pg_shdepend d WHERE d.refclassid = 'pg_authid'::regclass AND d.refobjid = p_role
                    AND d.dbid IN (0, (SELECT oid FROM pg_database WHERE datname = current_database())))
    AND NOT EXISTS (SELECT 1 FROM pg_auth_members m WHERE m.member = p_role)
    AND NOT EXISTS (SELECT 1 FROM pg_auth_members m JOIN pg_roles o ON o.oid = m.member, kernel.settings s
                    WHERE m.roleid = p_role AND (o.rolname <> s.owner OR NOT m.admin_option)
                      AND NOT (m.member IS NOT DISTINCT FROM kernel.bootstrap_admin() AND m.admin_option AND NOT m.inherit_option AND NOT m.set_option))
  FROM pg_roles r WHERE r.oid = p_role $$;

-- The five check views with those three edits made (the role list of every one; checks 18, 37 and 43 of the core). Written out as
-- they read after kernel_0023 with the edits applied (pg_get_viewdef text), so the revision runs no dynamic SQL.
--   role list: AND (r.oid IS DISTINCT FROM kernel.bootstrap_admin())
--   checks 18, 37: AND (NOT ((m.member IS NOT DISTINCT FROM kernel.bootstrap_admin()) AND m.admin_option AND (NOT m.inherit_option) AND (NOT m.set_option)))
--     (IS NOT DISTINCT FROM: with no bootstrap administrator the test must be false, never NULL, or NOT would hide the row)
--   check 43: (NOT (kernel.bootstrap_admin() IS NOT NULL)) AND before the managed-profile test
CREATE OR REPLACE VIEW kernel.rls_violations_core WITH (security_invoker = true) AS
WITH s AS (
         SELECT settings.one,
            settings.owner,
            settings.migrate,
            settings.app_group,
            settings.jobs,
            settings.role_prefix,
            settings.vetted_prefix,
            settings.backup,
            settings.restore,
            settings.ro,
            settings.services,
            settings.admins,
            settings.extensions,
            settings.org_id_pattern,
            settings.importer
           FROM kernel.settings
        ), nsp AS (
         SELECT n.oid,
            n.nspname
           FROM pg_namespace n
          WHERE ((n.nspname <> ALL (ARRAY['pg_catalog'::name, 'information_schema'::name, 'pg_toast'::name])) AND (n.nspname !~~ 'pg_temp%'::text) AND (n.nspname !~~ 'pg_toast_temp%'::text))
        ), app_nsp AS (
         SELECT nsp.oid,
            nsp.nspname
           FROM nsp
          WHERE (nsp.nspname <> 'kernel'::name)
        ), ext_objs AS (
         SELECT d.objid
           FROM (pg_depend d
             JOIN pg_extension e ON ((e.oid = d.refobjid))),
            s
          WHERE ((d.deptype = 'e'::"char") AND (d.refclassid = ('pg_extension'::regclass)::oid) AND (d.classid = ANY (ARRAY[('pg_class'::regclass)::oid, ('pg_proc'::regclass)::oid])) AND (e.extname = ANY (s.extensions)))
        ), grp AS (
         SELECT r.oid
           FROM pg_roles r,
            s
          WHERE (r.rolname = s.app_group)
        ), svc AS (
         SELECT r.oid
           FROM pg_roles r,
            s
          WHERE (r.rolname = ANY (s.services))
        ), logins AS (
         SELECT pg_roles.oid,
            pg_roles.rolname
           FROM pg_roles
          WHERE (pg_roles.rolcanlogin OR (pg_roles.rolname IN ( SELECT unnest(restore_window.disabled_logins) AS unnest
                   FROM kernel.restore_window)))
        ), admin_roles AS (
         SELECT r.oid
           FROM pg_roles r,
            s
          WHERE (r.rolname = ANY (s.admins))
        ), trusted AS (
         SELECT r.oid
           FROM pg_roles r,
            s
          WHERE ((r.rolname = s.owner) OR (r.rolname = s.migrate) OR (r.rolname = s.backup) OR (r.rolname = s.restore) OR (r.rolname ~~ (s.vetted_prefix || '%'::text)))
        ), rall AS (
         SELECT r.oid
           FROM pg_roles r
          WHERE ((NOT r.rolsuper) AND (r.rolname !~~ 'pg\_%'::text) AND (r.oid IS DISTINCT FROM kernel.bootstrap_admin()))
        ), actors AS (
         SELECT rall.oid
           FROM rall
          WHERE ((NOT (rall.oid IN ( SELECT trusted.oid
                   FROM trusted))) AND (NOT (rall.oid IN ( SELECT admin_roles.oid
                   FROM admin_roles))))
        ), reach AS (
         WITH RECURSIVE t(actor, role, all_inherit) AS (
                 SELECT a.oid,
                    a.oid,
                    true AS "?column?"
                   FROM rall a
                UNION
                 SELECT t_1.actor,
                    m.roleid,
                    (t_1.all_inherit AND m.inherit_option)
                   FROM (t t_1
                     JOIN pg_auth_members m ON ((m.member = t_1.role)))
                )
         SELECT t.actor,
            t.role,
            t.all_inherit
           FROM t
        ), reach_roles AS (
         SELECT DISTINCT reach.role AS oid
           FROM reach
          WHERE (reach.actor IN ( SELECT actors.oid
                   FROM actors))
        ), vetted_roles AS (
         SELECT r.oid,
            r.rolname
           FROM pg_roles r,
            s
          WHERE (r.rolname ~~ (s.vetted_prefix || '%'::text))
        ), rels AS (
         SELECT (c.oid)::regclass AS rel,
            c.oid,
            c.relname,
            n.nspname,
            c.relkind,
            c.relispartition,
            c.relrowsecurity,
            c.relforcerowsecurity,
            c.relowner,
            c.relacl,
            ( SELECT g.rel
                   FROM kernel.rls_registry g
                  WHERE ((c.oid = (g.rel)::oid) OR (c.oid IN ( SELECT pg_partition_tree.relid
                           FROM pg_partition_tree(g.rel) pg_partition_tree(relid, parentrelid, isleaf, level))))
                 LIMIT 1) AS root
           FROM (pg_class c
             JOIN app_nsp n ON ((n.oid = c.relnamespace)))
          WHERE (c.relkind = ANY (ARRAY['r'::"char", 'p'::"char", 'f'::"char", 'm'::"char"]))
        ), reg AS (
         SELECT rels.rel,
            rels.oid,
            rels.relname,
            rels.nspname,
            rels.relkind,
            rels.relispartition,
            rels.relrowsecurity,
            rels.relforcerowsecurity,
            rels.relowner,
            rels.relacl,
            rels.root,
            g.class,
            g.jobs_access,
            g.vetted_read,
            g.vetted_write,
            g.import_access
           FROM (rels
             JOIN kernel.rls_registry g ON (((g.rel)::oid = (rels.root)::oid)))
          WHERE (g.class <> ALL (ARRAY['global'::text, 'internal'::text]))
        ), glob AS (
         SELECT rels.rel,
            rels.oid,
            rels.relname,
            rels.nspname,
            rels.relkind,
            rels.relispartition,
            rels.relrowsecurity,
            rels.relforcerowsecurity,
            rels.relowner,
            rels.relacl,
            rels.root
           FROM (rels
             JOIN kernel.rls_registry g ON (((g.rel)::oid = (rels.root)::oid)))
          WHERE (g.class = 'global'::text)
        ), exempt AS (
         SELECT e.rel,
            e.conname,
            e.condef_md5,
            e.value_source,
            e.reason,
            e.reviewed_by,
            e.reviewed_at,
            c.oid AS conoid,
            c.conindid,
            md5(pg_get_constraintdef(c.oid)) AS cur_md5
           FROM (kernel.rls_exemptions e
             LEFT JOIN pg_constraint c ON (((c.conrelid = (e.rel)::oid) AND (c.conname = e.conname))))
        ), expected AS (
         SELECT reg.oid,
            t.name,
            t.cmd,
            t.qual,
            t.with_check,
                CASE t.name
                    WHEN 'jobs_all'::text THEN s.jobs
                    WHEN 'import_all'::text THEN s.importer
                    WHEN 'vetted_read'::text THEN ((s.vetted_prefix || (reg.nspname)::text))::name
                    WHEN 'vetted_write'::text THEN ((s.vetted_prefix || (reg.nspname)::text))::name
                    WHEN 'backup_read'::text THEN s.backup
                    WHEN 'restore_insert'::text THEN s.restore
                    ELSE s.app_group
                END AS role
           FROM reg,
            s,
            kernel.policy_template t
          WHERE ((t.name = ANY (ARRAY['org_scope'::text, 'backup_read'::text, 'restore_insert'::text])) OR ((t.name = 'lineage_read'::text) AND (reg.class = 'lineage'::text)) OR ((t.name = 'jobs_all'::text) AND reg.jobs_access) OR ((t.name = 'import_all'::text) AND reg.import_access) OR ((t.name = 'vetted_read'::text) AND reg.vetted_read) OR ((t.name = 'vetted_write'::text) AND reg.vetted_write))
        ), actual AS (
         SELECT p.polrelid AS oid,
            p.polname AS name,
            p.polcmd AS cmd,
            p.polpermissive,
            pg_get_expr(p.polqual, p.polrelid) AS qual,
            pg_get_expr(p.polwithcheck, p.polrelid) AS with_check,
            ( SELECT array_agg(pg_roles.rolname ORDER BY pg_roles.rolname) AS array_agg
                   FROM pg_roles
                  WHERE (pg_roles.oid = ANY (p.polroles))) AS roles
           FROM pg_policy p
        ), fn AS (
         SELECT p.oid,
            ((p.oid)::regprocedure)::text AS name,
            p.proname,
            p.provolatile,
            p.proowner,
            p.prosrc,
            n.nspname,
            g.class,
            g.writer,
            COALESCE(p.proacl, acldefault('f'::"char", p.proowner)) AS acl
           FROM ((pg_proc p
             JOIN nsp n ON ((n.oid = p.pronamespace)))
             LEFT JOIN kernel.function_registry g ON (((g.proc)::oid = p.oid)))
          WHERE p.prosecdef
        ), can_exec AS (
         SELECT fn.oid AS fn,
            r.oid AS role
           FROM fn,
            ( SELECT actors.oid
                   FROM actors
                UNION
                 SELECT r2.oid
                   FROM pg_roles r2,
                    s
                  WHERE ((r2.rolname = s.backup) OR (r2.rolname = s.restore))) r
          WHERE (EXISTS ( SELECT 1
                   FROM aclexplode(fn.acl) x(grantor, grantee, privilege_type, is_grantable)
                  WHERE ((x.privilege_type = 'EXECUTE'::text) AND (x.grantee <> (0)::oid) AND (x.grantee IN ( SELECT reach.role
                           FROM reach
                          WHERE (reach.actor = r.oid))))))
        ), cat AS (
         SELECT '01 unregistered relation'::text AS rule,
            (rels.rel)::text AS object
           FROM rels
          WHERE ((rels.root IS NULL) AND (NOT (rels.oid IN ( SELECT ext_objs.objid
                   FROM ext_objs))))
        UNION ALL
         SELECT '01 internal relation has an org_id column'::text AS text,
            (rels.rel)::text AS rel
           FROM (rels
             JOIN kernel.rls_registry g ON ((((g.rel)::oid = (rels.root)::oid) AND (g.class = 'internal'::text))))
          WHERE (EXISTS ( SELECT 1
                   FROM pg_attribute a
                  WHERE ((a.attrelid = rels.oid) AND (a.attname = 'org_id'::name) AND (NOT a.attisdropped))))
        UNION ALL
         SELECT '02 RLS not enabled+forced'::text AS text,
            (reg.rel)::text AS rel
           FROM reg
          WHERE (NOT (reg.relrowsecurity AND reg.relforcerowsecurity))
        UNION ALL
         SELECT ('03 missing expected policy '::text || e.name),
            ((e.oid)::regclass)::text AS oid
           FROM expected e
          WHERE (NOT (EXISTS ( SELECT 1
                   FROM actual a
                  WHERE ((a.oid = e.oid) AND (a.name = e.name)))))
        UNION ALL
         SELECT ('04 unexpected policy '::text || (a.name)::text),
            ((a.oid)::regclass)::text AS oid
           FROM (actual a
             JOIN rels r ON ((r.oid = a.oid)))
          WHERE (NOT (EXISTS ( SELECT 1
                   FROM expected e
                  WHERE ((e.oid = a.oid) AND (e.name = a.name)))))
        UNION ALL
         SELECT ('05 policy differs from template '::text || (a.name)::text),
            ((a.oid)::regclass)::text AS oid
           FROM (actual a
             JOIN expected e ON (((e.oid = a.oid) AND (e.name = a.name))))
          WHERE ((a.cmd <> e.cmd) OR (NOT a.polpermissive) OR (a.qual IS DISTINCT FROM e.qual) OR (a.with_check IS DISTINCT FROM e.with_check) OR (a.roles IS DISTINCT FROM ARRAY[e.role]))
        UNION ALL
         SELECT '06 missing org_id CHECK (not empty + format)'::text AS text,
            (reg.rel)::text AS rel
           FROM reg
          WHERE (((reg.rel)::oid = (reg.root)::oid) AND (( SELECT count(*) AS count
                   FROM pg_constraint k
                  WHERE ((k.conrelid = reg.oid) AND (k.conname = ANY (ARRAY['org_id_not_empty'::name, 'org_id_format'::name])))) < 2))
        UNION ALL
         SELECT '06 org_id is not text NOT NULL'::text AS text,
            (reg.rel)::text AS rel
           FROM reg
          WHERE (((reg.rel)::oid = (reg.root)::oid) AND (NOT (EXISTS ( SELECT 1
                   FROM pg_attribute a
                  WHERE ((a.attrelid = reg.oid) AND (a.attname = 'org_id'::name) AND (a.atttypid = ('text'::regtype)::oid) AND a.attnotnull AND (NOT a.attisdropped))))))
        UNION ALL
         SELECT '07 unique/PK/EXCLUDE index not led by org_id'::text AS text,
            ((i.indexrelid)::regclass)::text AS indexrelid
           FROM (reg
             JOIN pg_index i ON ((i.indrelid = reg.oid)))
          WHERE ((i.indisunique OR i.indisexclusion) AND (i.indkey[0] <> ( SELECT pg_attribute.attnum
                   FROM pg_attribute
                  WHERE ((pg_attribute.attrelid = reg.oid) AND (pg_attribute.attname = 'org_id'::name)))) AND (NOT (EXISTS ( SELECT 1
                   FROM exempt e
                  WHERE ((e.conindid = i.indexrelid) AND (e.cur_md5 = e.condef_md5))))))
        UNION ALL
         SELECT '07 exempted constraint missing or definition changed'::text AS text,
            (((e.rel)::text || '.'::text) || (e.conname)::text)
           FROM exempt e
          WHERE ((e.conoid IS NULL) OR (e.cur_md5 <> e.condef_md5))
        UNION ALL
         SELECT '07 exempted key has no server-generated value'::text AS text,
            (((e.rel)::text || '.'::text) || (e.conname)::text)
           FROM (exempt e
             JOIN pg_constraint c ON ((c.oid = e.conoid)))
          WHERE ((e.value_source = ANY (ARRAY['sequence'::text, 'random'::text])) AND (EXISTS ( SELECT 1
                   FROM unnest(c.conkey) k(k)
                  WHERE (NOT (EXISTS ( SELECT 1
                           FROM (pg_attribute a
                             LEFT JOIN pg_attrdef d ON (((d.adrelid = a.attrelid) AND (d.adnum = a.attnum))))
                          WHERE ((a.attrelid = c.conrelid) AND (a.attnum = k.k) AND ((a.attidentity <> ''::"char") OR ((e.value_source = 'sequence'::text) AND (pg_get_expr(d.adbin, d.adrelid) ~ 'nextval\('::text)) OR ((e.value_source = 'random'::text) AND (pg_get_expr(d.adbin, d.adrelid) ~ '(gen_random_uuid|gen_random_bytes|random)\('::text))))))))))
        UNION ALL
         SELECT '08 FK not led by org_id'::text AS text,
            (((k.conname)::text || ' on '::text) || ((k.conrelid)::regclass)::text)
           FROM pg_constraint k
          WHERE ((k.contype = 'f'::"char") AND (k.conparentid = (0)::oid) AND ((k.conrelid IN ( SELECT reg.oid
                   FROM reg)) OR (k.confrelid IN ( SELECT reg.oid
                   FROM reg))) AND ((( SELECT pg_attribute.attname
                   FROM pg_attribute
                  WHERE ((pg_attribute.attrelid = k.conrelid) AND (pg_attribute.attnum = k.conkey[1]))) IS DISTINCT FROM 'org_id'::name) OR (( SELECT pg_attribute.attname
                   FROM pg_attribute
                  WHERE ((pg_attribute.attrelid = k.confrelid) AND (pg_attribute.attnum = k.confkey[1]))) IS DISTINCT FROM 'org_id'::name)))
        UNION ALL
         SELECT '08 FK into an org-scoped table from an unregistered table'::text AS text,
            (((k.conname)::text || ' on '::text) || ((k.conrelid)::regclass)::text)
           FROM pg_constraint k
          WHERE ((k.contype = 'f'::"char") AND (k.conparentid = (0)::oid) AND (k.confrelid IN ( SELECT reg.oid
                   FROM reg)) AND (NOT (k.conrelid IN ( SELECT reg.oid
                   FROM reg))))
        UNION ALL
         SELECT '09 relation not owned by the owner role'::text AS text,
            ((c.oid)::regclass)::text AS oid
           FROM (pg_class c
             JOIN nsp n ON ((n.oid = c.relnamespace))),
            s
          WHERE ((c.relkind = ANY (ARRAY['r'::"char", 'p'::"char", 'v'::"char", 'm'::"char", 'S'::"char", 'f'::"char"])) AND (NOT (c.oid IN ( SELECT ext_objs.objid
                   FROM ext_objs))) AND (c.relowner <> ( SELECT pg_roles.oid
                   FROM pg_roles
                  WHERE (pg_roles.rolname = s.owner))))
        UNION ALL
         SELECT '09 schema not owned by the owner role'::text AS text,
            n.nspname
           FROM (nsp n
             JOIN pg_namespace x ON ((x.oid = n.oid))),
            s
          WHERE ((n.nspname <> 'public'::name) AND (x.nspowner <> ( SELECT pg_roles.oid
                   FROM pg_roles
                  WHERE (pg_roles.rolname = s.owner))))
        UNION ALL
         SELECT '10 partition has grants'::text AS text,
            (rels.rel)::text AS rel
           FROM rels,
            s,
            LATERAL aclexplode(rels.relacl) a(grantor, grantee, privilege_type, is_grantable)
          WHERE (rels.relispartition AND (a.grantee <> ( SELECT pg_roles.oid
                   FROM pg_roles
                  WHERE (pg_roles.rolname = s.owner))) AND (NOT ((a.grantee = (to_regrole((s.backup)::text))::oid) AND (a.privilege_type = 'SELECT'::text))) AND (NOT ((a.grantee = (to_regrole((s.restore)::text))::oid) AND (a.privilege_type = 'INSERT'::text))))
        UNION ALL
         SELECT '11 table privilege granted to the app group'::text AS text,
            (rels.rel)::text AS rel
           FROM rels,
            LATERAL aclexplode(rels.relacl) a(grantor, grantee, privilege_type, is_grantable)
          WHERE (a.grantee = ( SELECT grp.oid
                   FROM grp))
        UNION ALL
         SELECT ('12 app actor can '::text || a.privilege_type),
            (rels.rel)::text AS rel
           FROM rels,
            LATERAL aclexplode(rels.relacl) a(grantor, grantee, privilege_type, is_grantable)
          WHERE ((a.privilege_type = ANY (ARRAY['TRUNCATE'::text, 'REFERENCES'::text, 'TRIGGER'::text, 'MAINTAIN'::text])) AND ((a.grantee = (0)::oid) OR (a.grantee IN ( SELECT reach_roles.oid
                   FROM reach_roles))))
        UNION ALL
         SELECT '13 CREATE on schema for app actor/PUBLIC'::text AS text,
            n.nspname
           FROM (nsp n
             JOIN pg_namespace x ON ((x.oid = n.oid))),
            LATERAL aclexplode(COALESCE(x.nspacl, acldefault('n'::"char", x.nspowner))) a(grantor, grantee, privilege_type, is_grantable)
          WHERE ((a.privilege_type = 'CREATE'::text) AND ((a.grantee = (0)::oid) OR (a.grantee IN ( SELECT reach_roles.oid
                   FROM reach_roles))))
        UNION ALL
         SELECT (('13 '::text || a.privilege_type) || ' on database for app actor/PUBLIC'::text),
            d.datname
           FROM pg_database d,
            LATERAL aclexplode(COALESCE(d.datacl, acldefault('d'::"char", d.datdba))) a(grantor, grantee, privilege_type, is_grantable)
          WHERE ((d.datname = current_database()) AND (((a.privilege_type = 'CREATE'::text) AND ((a.grantee = (0)::oid) OR (a.grantee IN ( SELECT reach_roles.oid
                   FROM reach_roles)))) OR ((a.privilege_type = 'TEMPORARY'::text) AND (a.grantee = (0)::oid))))
        UNION ALL
         SELECT '14 view not security_invoker'::text AS text,
            ((c.oid)::regclass)::text AS oid
           FROM (pg_class c
             JOIN nsp n ON ((n.oid = c.relnamespace)))
          WHERE ((c.relkind = 'v'::"char") AND (NOT (EXISTS ( SELECT 1
                   FROM unnest(c.reloptions) o(o)
                  WHERE (o.o ~* '^security_invoker=(true|on|yes|1)$'::text)))))
        UNION ALL
         SELECT '14 materialized view'::text AS text,
            ((c.oid)::regclass)::text AS oid
           FROM (pg_class c
             JOIN nsp n ON ((n.oid = c.relnamespace)))
          WHERE (c.relkind = 'm'::"char")
        UNION ALL
         SELECT '15 SECURITY DEFINER not owned by its schema''s vetted role'::text AS text,
            fn.name
           FROM fn,
            s
          WHERE (fn.proowner IS DISTINCT FROM ( SELECT pg_roles.oid
                   FROM pg_roles
                  WHERE (pg_roles.rolname = (s.vetted_prefix || (fn.nspname)::text))))
        UNION ALL
         SELECT '16 SECURITY DEFINER search_path not pg_catalog,…,pg_temp'::text AS text,
            fn.name
           FROM fn
          WHERE (NOT (EXISTS ( SELECT 1
                   FROM pg_proc p,
                    LATERAL unnest(p.proconfig) cfg(cfg)
                  WHERE ((p.oid = fn.oid) AND (cfg.cfg ~ '^search_path=pg_catalog, '::text) AND (cfg.cfg ~ ', pg_temp$'::text) AND (cfg.cfg !~ 'pg_temp.*pg_temp'::text) AND (cfg.cfg !~ '"'::text)))))
        UNION ALL
         SELECT '17 SECURITY DEFINER executable by PUBLIC'::text AS text,
            fn.name
           FROM fn
          WHERE (EXISTS ( SELECT 1
                   FROM aclexplode(fn.acl) x(grantor, grantee, privilege_type, is_grantable)
                  WHERE ((x.grantee = (0)::oid) AND (x.privilege_type = 'EXECUTE'::text))))
        UNION ALL
         SELECT ('18 member of vetted role '::text || (v.rolname)::text),
            ((m.member)::regrole)::text AS member
           FROM (vetted_roles v
             JOIN pg_auth_members m ON ((m.roleid = v.oid))),
            s
          WHERE ((NOT ((m.member = (to_regrole((s.owner)::text))::oid) AND m.admin_option AND (NOT m.inherit_option) AND (NOT m.set_option))) AND (NOT ((NOT (m.member IS DISTINCT FROM kernel.bootstrap_admin())) AND m.admin_option AND (NOT m.inherit_option) AND (NOT m.set_option))))
        UNION ALL
         SELECT '18 member of owner role'::text AS text,
            ((m.member)::regrole)::text AS member
           FROM pg_auth_members m,
            s
          WHERE ((m.roleid = ( SELECT pg_roles.oid
                   FROM pg_roles
                  WHERE (pg_roles.rolname = s.owner))) AND (m.member <> ( SELECT pg_roles.oid
                   FROM pg_roles
                  WHERE (pg_roles.rolname = s.migrate))) AND (NOT ((NOT (m.member IS DISTINCT FROM kernel.bootstrap_admin())) AND m.admin_option AND (NOT m.inherit_option) AND (NOT m.set_option))))
        UNION ALL
         SELECT '19 can SET ROLE to the app group'::text AS text,
            ((m.member)::regrole)::text AS member
           FROM pg_auth_members m,
            grp
          WHERE ((m.roleid = grp.oid) AND m.set_option)
        UNION ALL
         SELECT '20 login is a member of several service roles'::text AS text,
            ((r.actor)::regrole)::text AS actor
           FROM (reach r
             JOIN svc ON ((svc.oid = r.role)))
          WHERE (r.actor IN ( SELECT logins.oid
                   FROM logins))
          GROUP BY r.actor
         HAVING (count(DISTINCT r.role) > 1)
        UNION ALL
         SELECT (('25 login inherits '::text || ((r.role)::regrole)::text) || ' (needs INHERIT FALSE)'::text),
            ((r.actor)::regrole)::text AS actor
           FROM reach r,
            s
          WHERE ((r.actor IN ( SELECT logins.oid
                   FROM logins)) AND (r.role <> r.actor) AND r.all_inherit AND ((r.role IN ( SELECT svc.oid
                   FROM svc)) OR ((r.role = ( SELECT grp.oid
                   FROM grp)) AND (r.actor IS DISTINCT FROM (to_regrole((s.ro)::text))::oid))))
        UNION ALL
         SELECT ('21 role/database setting '::text || cfg.cfg),
            COALESCE(r.rolname, '(all roles)'::name) AS "coalesce"
           FROM (pg_db_role_setting d
             LEFT JOIN pg_roles r ON ((r.oid = d.setrole))),
            LATERAL unnest(d.setconfig) cfg(cfg)
          WHERE (cfg.cfg ~* '^(app\.|role=|session_authorization=)'::text)
        UNION ALL
         SELECT '22 role can bypass RLS'::text AS text,
            r.rolname
           FROM pg_roles r,
            s
          WHERE (r.rolbypassrls AND (NOT r.rolsuper) AND (r.rolname !~ '^pg_'::text))
        UNION ALL
         SELECT '22 superuser other than the bootstrap superuser'::text AS text,
            r.rolname
           FROM pg_roles r
          WHERE (r.rolsuper AND (r.oid <> (10)::oid) AND (r.oid <> ( SELECT pg_database.datdba
                   FROM pg_database
                  WHERE (pg_database.datname = current_database()))))
        UNION ALL
         SELECT '23 invalid index'::text AS text,
            ((i.indexrelid)::regclass)::text AS indexrelid
           FROM ((pg_index i
             JOIN pg_class c ON ((c.oid = i.indexrelid)))
             JOIN app_nsp n ON ((n.oid = c.relnamespace)))
          WHERE (NOT i.indisvalid)
        UNION ALL
         SELECT '24 registry row for a dropped relation'::text AS text,
            ((g.rel)::oid)::text AS rel
           FROM kernel.rls_registry g
          WHERE (NOT (EXISTS ( SELECT 1
                   FROM pg_class c
                  WHERE (c.oid = (g.rel)::oid))))
        UNION ALL
         SELECT (('26 app actor/PUBLIC holds '::text || a.privilege_type) || ' on a global table'::text),
            (glob.rel)::text AS rel
           FROM glob,
            LATERAL aclexplode(glob.relacl) a(grantor, grantee, privilege_type, is_grantable)
          WHERE ((a.grantee = (0)::oid) OR (a.grantee IN ( SELECT reach_roles.oid
                   FROM reach_roles)))
        UNION ALL
         SELECT (('26 restore role holds '::text || a.privilege_type) || ' on a global table'::text),
            (glob.rel)::text AS rel
           FROM glob,
            s,
            LATERAL aclexplode(glob.relacl) a(grantor, grantee, privilege_type, is_grantable)
          WHERE (a.grantee = (to_regrole((s.restore)::text))::oid)
        UNION ALL
         SELECT ('27 backup role holds '::text || a.privilege_type),
            ((c.oid)::regclass)::text AS oid
           FROM (pg_class c
             JOIN nsp n ON ((n.oid = c.relnamespace))),
            s,
            LATERAL aclexplode(c.relacl) a(grantor, grantee, privilege_type, is_grantable)
          WHERE ((a.grantee = (to_regrole((s.backup)::text))::oid) AND (a.privilege_type <> 'SELECT'::text))
        UNION ALL
         SELECT '27 backup role owns or is a member'::text AS text,
            s.backup
           FROM s
          WHERE ((EXISTS ( SELECT 1
                   FROM pg_class
                  WHERE (pg_class.relowner = (to_regrole((s.backup)::text))::oid))) OR (EXISTS ( SELECT 1
                   FROM pg_auth_members
                  WHERE (pg_auth_members.member = (to_regrole((s.backup)::text))::oid))))
        UNION ALL
         SELECT ('28 restore role holds '::text || a.privilege_type),
            ((c.oid)::regclass)::text AS oid
           FROM (pg_class c
             JOIN nsp n ON ((n.oid = c.relnamespace))),
            s,
            LATERAL aclexplode(c.relacl) a(grantor, grantee, privilege_type, is_grantable)
          WHERE ((a.grantee = (to_regrole((s.restore)::text))::oid) AND (a.privilege_type <> 'INSERT'::text) AND (NOT ((c.oid = ('kernel.restore_window'::regclass)::oid) AND (a.privilege_type = 'SELECT'::text))))
        UNION ALL
         SELECT '28 restore role owns or is a member'::text AS text,
            s.restore
           FROM s,
            pg_roles r
          WHERE ((r.rolname = s.restore) AND ((EXISTS ( SELECT 1
                   FROM pg_class
                  WHERE (pg_class.relowner = r.oid))) OR (EXISTS ( SELECT 1
                   FROM pg_auth_members
                  WHERE (pg_auth_members.member = r.oid)))))
        UNION ALL
         SELECT '28 restore role can log in without an open window'::text AS text,
            s.restore
           FROM s,
            pg_roles r
          WHERE ((r.rolname = s.restore) AND r.rolcanlogin AND (NOT (EXISTS ( SELECT 1
                   FROM kernel.restore_window w
                  WHERE (w.until > now())))))
        UNION ALL
         SELECT '28 restore window expired and not closed'::text AS text,
            w.org
           FROM kernel.restore_window w
          WHERE (w.until <= now())
        UNION ALL
         SELECT (('29 app actor/PUBLIC holds '::text || a.privilege_type) || ' on an exempted table'::text),
            (e.rel)::text AS rel
           FROM kernel.rls_exemptions e,
            pg_class c,
            LATERAL aclexplode(c.relacl) a(grantor, grantee, privilege_type, is_grantable)
          WHERE ((c.oid = (e.rel)::oid) AND ((a.grantee = (0)::oid) OR (a.grantee IN ( SELECT reach_roles.oid
                   FROM reach_roles))))
        UNION ALL
         SELECT ('30 grant on kernel table to '::text || ((a.grantee)::regrole)::text),
            ((c.oid)::regclass)::text AS oid
           FROM (pg_class c
             JOIN pg_namespace n ON ((n.oid = c.relnamespace))),
            s,
            LATERAL aclexplode(c.relacl) a(grantor, grantee, privilege_type, is_grantable)
          WHERE ((n.nspname = 'kernel'::name) AND (c.relkind = ANY (ARRAY['r'::"char", 'p'::"char", 'v'::"char", 'm'::"char"])) AND (a.grantee <> c.relowner) AND (NOT ((c.relname = 'schema_epoch'::name) AND (a.privilege_type = 'SELECT'::text) AND (a.grantee IN ( SELECT svc.oid
                   FROM svc)))) AND (NOT ((c.relname = 'restore_window'::name) AND (a.privilege_type = 'SELECT'::text) AND (a.grantee = (to_regrole((s.restore)::text))::oid))))
        UNION ALL
         SELECT ('31 jobs-only function executable by '::text || ((x.role)::regrole)::text),
            fn.name
           FROM (fn
             JOIN can_exec x ON ((x.fn = fn.oid))),
            s
          WHERE ((fn.class = 'jobs'::text) AND (x.role <> fn.proowner) AND (x.role IS DISTINCT FROM (to_regrole((s.jobs)::text))::oid) AND (x.role IS DISTINCT FROM (to_regrole((s.importer)::text))::oid))
        UNION ALL
         SELECT '31 import/sweep function is not registered as jobs-only'::text AS text,
            fn.name
           FROM fn
          WHERE ((fn.proname ~ '^(import|sweep)_'::text) AND (fn.class IS DISTINCT FROM 'jobs'::text))
        UNION ALL
         SELECT '32 prologue function is not VOLATILE'::text AS text,
            fn.name
           FROM fn
          WHERE ((fn.class = 'prologue'::text) AND (fn.provolatile <> 'v'::"char"))
        UNION ALL
         SELECT '32 prologue fence does not assert transaction_isolation'::text AS text,
            fn.name
           FROM fn
          WHERE ((fn.class = 'prologue'::text) AND (fn.prosrc !~ 'transaction_isolation'::text))
        UNION ALL
         SELECT '32 prologue function executable by PUBLIC'::text AS text,
            fn.name
           FROM fn
          WHERE ((fn.class = 'prologue'::text) AND (EXISTS ( SELECT 1
                   FROM aclexplode(fn.acl) x(grantor, grantee, privilege_type, is_grantable)
                  WHERE ((x.grantee = (0)::oid) AND (x.privilege_type = 'EXECUTE'::text)))))
        UNION ALL
         SELECT ('32 prologue function executable by '::text || ((x.role)::regrole)::text),
            fn.name
           FROM (fn
             JOIN can_exec x ON ((x.fn = fn.oid))),
            s
          WHERE ((fn.class = 'prologue'::text) AND ((x.role IN ( SELECT grp.oid
                   FROM grp)) OR (x.role IN ( SELECT pg_roles.oid
                   FROM pg_roles
                  WHERE (pg_roles.rolname = ANY (ARRAY[s.backup, s.restore, s.ro]))))))
        UNION ALL
         SELECT ('33 write-class function executable by '::text || ((x.role)::regrole)::text),
            fn.name
           FROM (fn
             JOIN can_exec x ON ((x.fn = fn.oid)))
          WHERE ((fn.class = 'write'::text) AND (x.role <> fn.proowner) AND (x.role IS DISTINCT FROM (to_regrole((fn.writer)::text))::oid) AND (NOT (EXISTS ( SELECT 1
                   FROM pg_auth_members m
                  WHERE ((m.member = x.role) AND (m.roleid = (to_regrole((fn.writer)::text))::oid))))))
        UNION ALL
         SELECT '33 write-class function executable by PUBLIC'::text AS text,
            fn.name
           FROM fn
          WHERE ((fn.class = ANY (ARRAY['write'::text, 'jobs'::text])) AND (EXISTS ( SELECT 1
                   FROM aclexplode(fn.acl) x(grantor, grantee, privilege_type, is_grantable)
                  WHERE ((x.grantee = (0)::oid) AND (x.privilege_type = 'EXECUTE'::text)))))
        UNION ALL
         SELECT '34 write function not executable by its writer role'::text AS text,
            fn.name
           FROM fn
          WHERE ((fn.class = 'write'::text) AND (NOT (EXISTS ( SELECT 1
                   FROM can_exec x
                  WHERE ((x.fn = fn.oid) AND (x.role = (to_regrole((fn.writer)::text))::oid))))))
        UNION ALL
         SELECT '35 SECURITY DEFINER function not in kernel.function_registry'::text AS text,
            fn.name
           FROM fn
          WHERE (fn.class IS NULL)
        UNION ALL
         SELECT ('35 read-class function calls '::text || g.name),
            f.name
           FROM (fn f
             JOIN fn g ON (((g.class = ANY (ARRAY['write'::text, 'jobs'::text])) AND (f.prosrc ~* (('\m'::text || (g.proname)::text) || '\M'::text)))))
          WHERE (f.class = 'read'::text)
        UNION ALL
         SELECT '36 read-class function is VOLATILE'::text AS text,
            fn.name
           FROM fn
          WHERE ((fn.class = 'read'::text) AND (fn.provolatile = 'v'::"char"))
        UNION ALL
         SELECT '36 read-class function body has DML or dynamic SQL'::text AS text,
            fn.name
           FROM fn
          WHERE ((fn.class = 'read'::text) AND (fn.prosrc ~* '\m(insert\s+into|delete\s+from|truncate|execute|copy)\M|\mupdate\s+[a-z_."]+\s+set\M'::text))
        UNION ALL
         SELECT '36 registry row for a dropped function'::text AS text,
            ((g.proc)::oid)::text AS proc
           FROM kernel.function_registry g
          WHERE (NOT (EXISTS ( SELECT 1
                   FROM pg_proc p
                  WHERE (p.oid = (g.proc)::oid))))
        UNION ALL
         SELECT ((('37 unexpected membership '::text || ((m.member)::regrole)::text) || ' -> '::text) || ((m.roleid)::regrole)::text),
            ((m.member)::regrole)::text AS member
           FROM pg_auth_members m,
            s
          WHERE ((NOT (m.member IN ( SELECT pg_roles.oid
                   FROM pg_roles
                  WHERE (pg_roles.rolsuper OR (pg_roles.rolname ~~ 'pg\_%'::text))))) AND (NOT ((NOT (m.member IS DISTINCT FROM kernel.bootstrap_admin())) AND m.admin_option AND (NOT m.inherit_option) AND (NOT m.set_option))) AND (NOT ((m.member IN ( SELECT admin_roles.oid
                   FROM admin_roles)) AND ((m.roleid IN ( SELECT admin_roles.oid
                   FROM admin_roles)) OR (m.roleid IN ( SELECT pg_roles.oid
                   FROM pg_roles
                  WHERE (pg_roles.rolname ~~ 'pg\_%'::text)))))) AND (NOT (((m.member IN ( SELECT logins.oid
                   FROM logins)) AND (m.roleid IN ( SELECT svc.oid
                   FROM svc)) AND (NOT m.admin_option) AND (m.member = (to_regrole(((( SELECT pg_roles.rolname
                   FROM pg_roles
                  WHERE (pg_roles.oid = m.roleid)))::text || '_login'::text)))::oid) AND (NOT (m.member IN ( SELECT pg_roles.oid
                   FROM pg_roles
                  WHERE (pg_roles.rolname = ANY (ARRAY[s.jobs, s.ro, s.migrate, s.backup, s.restore])))))) OR ((m.member IN ( SELECT svc.oid
                   FROM svc)) AND (m.roleid = ( SELECT grp.oid
                   FROM grp))) OR ((m.member = (to_regrole((s.ro)::text))::oid) AND (m.roleid = ( SELECT grp.oid
                   FROM grp))) OR ((m.member = (to_regrole((s.migrate)::text))::oid) AND (m.roleid = (to_regrole((s.owner)::text))::oid)) OR ((m.member = (to_regrole((s.owner)::text))::oid) AND (m.roleid IN ( SELECT vetted_roles.oid
                   FROM vetted_roles)) AND m.admin_option AND (NOT m.inherit_option) AND (NOT m.set_option)))))
        UNION ALL
         SELECT ('37 application role reaches administrator role '::text || ((r.role)::regrole)::text),
            ((r.actor)::regrole)::text AS actor
           FROM reach r
          WHERE ((r.actor IN ( SELECT actors.oid
                   FROM actors)) AND (r.role IN ( SELECT admin_roles.oid
                   FROM admin_roles)))
        UNION ALL
         SELECT ('37 administrator role reaches application role '::text || ((r.role)::regrole)::text),
            ((r.actor)::regrole)::text AS actor
           FROM reach r
          WHERE ((r.actor IN ( SELECT admin_roles.oid
                   FROM admin_roles)) AND (r.role <> r.actor) AND (r.role IN ( SELECT actors.oid
                   FROM actors
                UNION
                 SELECT trusted.oid
                   FROM trusted)))
        UNION ALL
         SELECT '37 settings.admins lists an application role'::text AS text,
            r.rolname
           FROM pg_roles r,
            s
          WHERE ((r.oid IN ( SELECT admin_roles.oid
                   FROM admin_roles)) AND ((r.rolname ~~ (s.role_prefix || '%'::text)) OR (r.oid IN ( SELECT trusted.oid
                   FROM trusted
                UNION
                 SELECT svc.oid
                   FROM svc
                UNION
                 SELECT grp.oid
                   FROM grp)) OR ((r.rolname = s.jobs) OR (r.rolname = s.ro))))
        UNION ALL
         SELECT ('38 column-level privilege on '::text || (a.attname)::text),
            ((c.oid)::regclass)::text AS oid
           FROM ((pg_attribute a
             JOIN pg_class c ON ((c.oid = a.attrelid)))
             JOIN nsp n ON ((n.oid = c.relnamespace))),
            LATERAL aclexplode(a.attacl) x(grantor, grantee, privilege_type, is_grantable)
          WHERE ((a.attacl IS NOT NULL) AND (NOT a.attisdropped) AND (x.grantee <> c.relowner))
        UNION ALL
         SELECT '39 role that owns objects or must be NOLOGIN can log in'::text AS text,
            r.rolname
           FROM pg_roles r,
            s
          WHERE (r.rolcanlogin AND (NOT r.rolsuper) AND ((r.rolname = s.owner) OR (r.rolname = s.app_group) OR (r.oid IN ( SELECT svc.oid
                   FROM svc)) OR (r.oid IN ( SELECT vetted_roles.oid
                   FROM vetted_roles)) OR (r.oid IN ( SELECT c.relowner
                   FROM (pg_class c
                     JOIN nsp n ON ((n.oid = c.relnamespace)))
                  WHERE ((c.relkind = ANY (ARRAY['r'::"char", 'p'::"char", 'v'::"char", 'm'::"char", 'S'::"char", 'f'::"char"])) AND (NOT (c.oid IN ( SELECT ext_objs.objid
                           FROM ext_objs)))))) OR (r.oid IN ( SELECT p.proowner
                   FROM (pg_proc p
                     JOIN nsp n ON ((n.oid = p.pronamespace)))
                  WHERE p.prosecdef)) OR (r.oid IN ( SELECT n.nspowner
                   FROM (nsp x
                     JOIN pg_namespace n ON ((n.oid = x.oid)))))))
        UNION ALL
         SELECT ('40 rewrite rule '::text || (w.rulename)::text),
            ((c.oid)::regclass)::text AS oid
           FROM ((pg_rewrite w
             JOIN pg_class c ON ((c.oid = w.ev_class)))
             JOIN nsp n ON ((n.oid = c.relnamespace)))
          WHERE ((w.rulename <> '_RETURN'::name) AND (NOT (c.oid IN ( SELECT ext_objs.objid
                   FROM ext_objs))))
        UNION ALL
         SELECT ('41 role holds '::text || x.attr),
            r.rolname
           FROM pg_roles r,
            LATERAL ( VALUES ('REPLICATION'::text,r.rolreplication), ('CREATEROLE'::text,r.rolcreaterole), ('CREATEDB'::text,r.rolcreatedb)) x(attr, held)
          WHERE (x.held AND (r.oid IN ( SELECT rall.oid
                   FROM rall)) AND (NOT (r.oid IN ( SELECT admin_roles.oid
                   FROM admin_roles))))
        UNION ALL
         SELECT (((('42 vetted role '::text || ((a.grantee)::regrole)::text) || ' holds '::text) || a.privilege_type) || ' outside its schema'::text),
            ((c.oid)::regclass)::text AS oid
           FROM (pg_class c
             JOIN nsp n ON ((n.oid = c.relnamespace))),
            s,
            LATERAL aclexplode(c.relacl) a(grantor, grantee, privilege_type, is_grantable)
          WHERE ((a.grantee IN ( SELECT vetted_roles.oid
                   FROM vetted_roles)) AND (a.grantee <> (COALESCE(to_regrole((s.vetted_prefix || (n.nspname)::text)), ((0)::oid)::regrole))::oid))
        UNION ALL
         SELECT '43 large objects exist'::text AS text,
            (count(*))::text AS count
           FROM pg_largeobject_metadata
         HAVING (count(*) > 0)
        UNION ALL
         SELECT ('43 role can use large object function '::text || f.sig),
            ((a.oid)::regrole)::text AS oid
           FROM actors a,
            unnest(ARRAY['lo_create(oid)'::text, 'lo_creat(integer)'::text, 'lo_from_bytea(oid,bytea)'::text, 'lo_put(oid,bigint,bytea)'::text, 'lo_get(oid)'::text, 'lo_get(oid,bigint,integer)'::text, 'lo_unlink(oid)'::text, 'lo_open(oid,integer)'::text, 'loread(integer,integer)'::text, 'lowrite(integer,bytea)'::text]) f(sig)
          WHERE ((to_regprocedure(('pg_catalog.'::text || f.sig)) IS NOT NULL) AND has_function_privilege(a.oid, (to_regprocedure(('pg_catalog.'::text || f.sig)))::oid, 'EXECUTE'::text) AND (NOT (kernel.bootstrap_admin() IS NOT NULL)) AND (NOT COALESCE(( SELECT ((deployment_profile.profile ->> 'managed'::text))::boolean AS bool
                   FROM platform.deployment_profile), false)))
        UNION ALL
         SELECT '44 consumer inbox primary key is not (org_id, message_id)'::text AS text,
            ((c.oid)::regclass)::text AS oid
           FROM pg_class c
          WHERE ((c.oid = (to_regclass('platform.consumer_inbox'::text))::oid) AND (( SELECT array_agg(a.attname ORDER BY k.ord) AS array_agg
                   FROM ((pg_constraint pc
                     CROSS JOIN LATERAL unnest(pc.conkey) WITH ORDINALITY k(attnum, ord))
                     JOIN pg_attribute a ON (((a.attrelid = pc.conrelid) AND (a.attnum = k.attnum))))
                  WHERE ((pc.conrelid = c.oid) AND (pc.contype = 'p'::"char"))) IS DISTINCT FROM ARRAY['org_id'::name, 'message_id'::name]))
        UNION ALL
         SELECT '45 foreign table'::text AS text,
            ((c.oid)::regclass)::text AS oid
           FROM (pg_class c
             JOIN nsp n ON ((n.oid = c.relnamespace)))
          WHERE (c.relkind = 'f'::"char")
        UNION ALL
         SELECT '45 foreign server'::text AS text,
            pg_foreign_server.srvname
           FROM pg_foreign_server
        UNION ALL
         SELECT '45 user mapping'::text AS text,
            (((um.srvname)::text || ' for '::text) || (COALESCE(um.usename, 'PUBLIC'::name))::text)
           FROM pg_user_mappings um
        UNION ALL
         SELECT '46 app actor/PUBLIC can setval a sequence'::text AS text,
            ((c.oid)::regclass)::text AS oid
           FROM (pg_class c
             JOIN nsp n ON ((n.oid = c.relnamespace))),
            LATERAL aclexplode(c.relacl) a(grantor, grantee, privilege_type, is_grantable)
          WHERE ((c.relkind = 'S'::"char") AND (a.privilege_type = 'UPDATE'::text) AND ((a.grantee = (0)::oid) OR (a.grantee IN ( SELECT reach_roles.oid
                   FROM reach_roles))))
        UNION ALL
         SELECT '47 default privileges'::text AS text,
            ((COALESCE(((d.defaclrole)::regrole)::text, ''::text) || ' '::text) || (d.defaclobjtype)::text)
           FROM pg_default_acl d
        UNION ALL
         SELECT '48 event trigger'::text AS text,
            pg_event_trigger.evtname
           FROM pg_event_trigger
        UNION ALL
         SELECT '48 subscription'::text AS text,
            pg_subscription.subname
           FROM pg_subscription
        UNION ALL
         SELECT '48 publication'::text AS text,
            p.pubname
           FROM pg_publication p
          WHERE (NOT ((p.pubname = 'pipeshub_outbox'::name) AND (NOT p.puballtables) AND (NOT (EXISTS ( SELECT 1
                   FROM pg_publication_rel r
                  WHERE ((r.prpubid = p.oid) AND (r.prrelid <> (COALESCE(to_regclass('platform.outbox'::text), ((0)::oid)::regclass))::oid)))))))
        UNION ALL
         SELECT '49 LEAKPROOF function'::text AS text,
            ((p.oid)::regprocedure)::text AS oid
           FROM (pg_proc p
             JOIN nsp n ON ((n.oid = p.pronamespace)))
          WHERE (p.proleakproof AND (NOT (p.oid IN ( SELECT ext_objs.objid
                   FROM ext_objs))))
        UNION ALL
         SELECT '51 extension not allowed'::text AS text,
            e.extname
           FROM pg_extension e,
            s
          WHERE (NOT (e.extname = ANY (s.extensions)))
        UNION ALL
         SELECT '32 prologue function is not VOLATILE'::text AS text,
            ((p.oid)::regprocedure)::text AS oid
           FROM (pg_proc p
             JOIN kernel.function_registry g ON (((g.proc)::oid = p.oid)))
          WHERE ((g.class = 'prologue'::text) AND (NOT p.prosecdef) AND (p.provolatile <> 'v'::"char"))
        UNION ALL
         SELECT '32 prologue fence does not assert transaction_isolation'::text AS text,
            ((p.oid)::regprocedure)::text AS oid
           FROM (pg_proc p
             JOIN kernel.function_registry g ON (((g.proc)::oid = p.oid)))
          WHERE ((g.class = 'prologue'::text) AND (NOT p.prosecdef) AND (p.prosrc !~ 'transaction_isolation'::text))
        UNION ALL
         SELECT '32 prologue function executable by PUBLIC'::text AS text,
            ((p.oid)::regprocedure)::text AS oid
           FROM (pg_proc p
             JOIN kernel.function_registry g ON (((g.proc)::oid = p.oid)))
          WHERE ((g.class = 'prologue'::text) AND (NOT p.prosecdef) AND (EXISTS ( SELECT 1
                   FROM aclexplode(COALESCE(p.proacl, acldefault('f'::"char", p.proowner))) x(grantor, grantee, privilege_type, is_grantable)
                  WHERE ((x.grantee = (0)::oid) AND (x.privilege_type = 'EXECUTE'::text)))))
        UNION ALL
         SELECT (('52 user-created '::text || x.kind) || ' in a system schema'::text),
            x.name
           FROM ( SELECT 'relation'::text AS kind,
                    ((c.oid)::regclass)::text AS name
                   FROM (pg_class c
                     JOIN pg_namespace n ON ((n.oid = c.relnamespace)))
                  WHERE ((n.nspname = ANY (ARRAY['pg_catalog'::name, 'information_schema'::name])) AND (c.oid >= (16384)::oid))
                UNION ALL
                 SELECT 'function'::text AS text,
                    ((p.oid)::regprocedure)::text AS oid
                   FROM (pg_proc p
                     JOIN pg_namespace n ON ((n.oid = p.pronamespace)))
                  WHERE ((n.nspname = ANY (ARRAY['pg_catalog'::name, 'information_schema'::name])) AND (p.oid >= (16384)::oid))
                UNION ALL
                 SELECT 'type'::text AS text,
                    ((t.oid)::regtype)::text AS oid
                   FROM (pg_type t
                     JOIN pg_namespace n ON ((n.oid = t.typnamespace)))
                  WHERE ((n.nspname = ANY (ARRAY['pg_catalog'::name, 'information_schema'::name])) AND (t.oid >= (16384)::oid))) x
        UNION ALL
         SELECT (('53 app actor/PUBLIC holds '::text || a.privilege_type) || ' on system relation'::text),
            ((c.oid)::regclass)::text AS oid
           FROM (pg_class c
             JOIN pg_namespace n ON ((n.oid = c.relnamespace))),
            LATERAL aclexplode(c.relacl) a(grantor, grantee, privilege_type, is_grantable)
          WHERE ((n.nspname = ANY (ARRAY['pg_catalog'::name, 'information_schema'::name])) AND ((a.grantee = (0)::oid) OR (a.grantee IN ( SELECT reach_roles.oid
                   FROM reach_roles))) AND (c.relacl IS DISTINCT FROM ( SELECT i.initprivs
                   FROM pg_init_privs i
                  WHERE ((i.objoid = c.oid) AND (i.classoid = ('pg_class'::regclass)::oid) AND (i.objsubid = 0)))) AND (NOT ((a.grantee = (0)::oid) AND (a.privilege_type = ANY (ARRAY['SELECT'::text, 'USAGE'::text, 'EXECUTE'::text])) AND (c.relnamespace = ('information_schema'::regnamespace)::oid))))
        UNION ALL
         SELECT ('53 app actor/PUBLIC can execute system function '::text || (p.proname)::text),
            ((p.oid)::regprocedure)::text AS oid
           FROM (pg_proc p
             JOIN pg_namespace n ON ((n.oid = p.pronamespace))),
            LATERAL aclexplode(p.proacl) a(grantor, grantee, privilege_type, is_grantable)
          WHERE ((n.nspname = ANY (ARRAY['pg_catalog'::name, 'information_schema'::name])) AND (a.privilege_type = 'EXECUTE'::text) AND ((a.grantee = (0)::oid) OR (a.grantee IN ( SELECT reach_roles.oid
                   FROM reach_roles))) AND (p.proname !~ '^(lo_|lo(read|write)$)'::text) AND (p.proacl IS DISTINCT FROM ( SELECT i.initprivs
                   FROM pg_init_privs i
                  WHERE ((i.objoid = p.oid) AND (i.classoid = ('pg_proc'::regclass)::oid) AND (i.objsubid = 0)))))
        UNION ALL
         SELECT ((('54 app actor/PUBLIC holds '::text || a.privilege_type) || ' on parameter '::text) || pa.parname),
            pa.parname
           FROM pg_parameter_acl pa,
            LATERAL aclexplode(pa.paracl) a(grantor, grantee, privilege_type, is_grantable)
          WHERE ((a.grantee = (0)::oid) OR (a.grantee IN ( SELECT reach_roles.oid
                   FROM reach_roles)))
        UNION ALL
         SELECT ((('54 app actor/PUBLIC holds '::text || a.privilege_type) || ' on tablespace '::text) || (t.spcname)::text),
            t.spcname
           FROM pg_tablespace t,
            LATERAL aclexplode(COALESCE(t.spcacl, acldefault('t'::"char", t.spcowner))) a(grantor, grantee, privilege_type, is_grantable)
          WHERE ((a.grantee <> t.spcowner) AND ((a.grantee = (0)::oid) OR (a.grantee IN ( SELECT reach_roles.oid
                   FROM reach_roles))))
        )
 SELECT rule,
    object
   FROM cat;
CREATE OR REPLACE VIEW kernel.vetted_violations WITH (security_invoker = true) AS
WITH s AS (
         SELECT settings.one,
            settings.owner,
            settings.migrate,
            settings.app_group,
            settings.jobs,
            settings.role_prefix,
            settings.vetted_prefix,
            settings.backup,
            settings.restore,
            settings.ro,
            settings.services,
            settings.admins,
            settings.extensions,
            settings.org_id_pattern
           FROM kernel.settings
        ), nsp AS (
         SELECT n.oid,
            n.nspname
           FROM pg_namespace n
          WHERE ((n.nspname <> ALL (ARRAY['pg_catalog'::name, 'information_schema'::name, 'pg_toast'::name])) AND (n.nspname !~~ 'pg_temp%'::text) AND (n.nspname !~~ 'pg_toast_temp%'::text))
        ), app_nsp AS (
         SELECT nsp.oid,
            nsp.nspname
           FROM nsp
          WHERE (nsp.nspname <> 'kernel'::name)
        ), ext_objs AS (
         SELECT d.objid
           FROM (pg_depend d
             JOIN pg_extension e ON ((e.oid = d.refobjid))),
            s
          WHERE ((d.deptype = 'e'::"char") AND (d.refclassid = ('pg_extension'::regclass)::oid) AND (d.classid = ANY (ARRAY[('pg_class'::regclass)::oid, ('pg_proc'::regclass)::oid])) AND (e.extname = ANY (s.extensions)))
        ), grp AS (
         SELECT r.oid
           FROM pg_roles r,
            s
          WHERE (r.rolname = s.app_group)
        ), svc AS (
         SELECT r.oid
           FROM pg_roles r,
            s
          WHERE (r.rolname = ANY (s.services))
        ), logins AS (
         SELECT pg_roles.oid,
            pg_roles.rolname
           FROM pg_roles
          WHERE (pg_roles.rolcanlogin OR (pg_roles.rolname IN ( SELECT unnest(restore_window.disabled_logins) AS unnest
                   FROM kernel.restore_window)))
        ), admin_roles AS (
         SELECT r.oid
           FROM pg_roles r,
            s
          WHERE (r.rolname = ANY (s.admins))
        ), trusted AS (
         SELECT r.oid
           FROM pg_roles r,
            s
          WHERE ((r.rolname = s.owner) OR (r.rolname = s.migrate) OR (r.rolname = s.backup) OR (r.rolname = s.restore) OR (r.rolname ~~ (s.vetted_prefix || '%'::text)))
        ), rall AS (
         SELECT r.oid
           FROM pg_roles r
          WHERE ((NOT r.rolsuper) AND (r.rolname !~~ 'pg\_%'::text) AND (r.oid IS DISTINCT FROM kernel.bootstrap_admin()))
        ), actors AS (
         SELECT rall.oid
           FROM rall
          WHERE ((NOT (rall.oid IN ( SELECT trusted.oid
                   FROM trusted))) AND (NOT (rall.oid IN ( SELECT admin_roles.oid
                   FROM admin_roles))))
        ), reach AS (
         WITH RECURSIVE t(actor, role, all_inherit) AS (
                 SELECT a.oid,
                    a.oid,
                    true AS "?column?"
                   FROM rall a
                UNION
                 SELECT t_1.actor,
                    m.roleid,
                    (t_1.all_inherit AND m.inherit_option)
                   FROM (t t_1
                     JOIN pg_auth_members m ON ((m.member = t_1.role)))
                )
         SELECT t.actor,
            t.role,
            t.all_inherit
           FROM t
        ), reach_roles AS (
         SELECT DISTINCT reach.role AS oid
           FROM reach
          WHERE (reach.actor IN ( SELECT actors.oid
                   FROM actors))
        ), vetted_roles AS (
         SELECT r.oid,
            r.rolname
           FROM pg_roles r,
            s
          WHERE (r.rolname ~~ (s.vetted_prefix || '%'::text))
        ), rels AS (
         SELECT (c.oid)::regclass AS rel,
            c.oid,
            c.relname,
            n.nspname,
            c.relkind,
            c.relispartition,
            c.relrowsecurity,
            c.relforcerowsecurity,
            c.relowner,
            c.relacl,
            ( SELECT g.rel
                   FROM kernel.rls_registry g
                  WHERE ((c.oid = (g.rel)::oid) OR (c.oid IN ( SELECT pg_partition_tree.relid
                           FROM pg_partition_tree(g.rel) pg_partition_tree(relid, parentrelid, isleaf, level))))
                 LIMIT 1) AS root
           FROM (pg_class c
             JOIN app_nsp n ON ((n.oid = c.relnamespace)))
          WHERE (c.relkind = ANY (ARRAY['r'::"char", 'p'::"char", 'f'::"char", 'm'::"char"]))
        ), reg AS (
         SELECT rels.rel,
            rels.oid,
            rels.relname,
            rels.nspname,
            rels.relkind,
            rels.relispartition,
            rels.relrowsecurity,
            rels.relforcerowsecurity,
            rels.relowner,
            rels.relacl,
            rels.root,
            g.class,
            g.jobs_access,
            g.vetted_read,
            g.vetted_write
           FROM (rels
             JOIN kernel.rls_registry g ON (((g.rel)::oid = (rels.root)::oid)))
          WHERE (g.class <> ALL (ARRAY['global'::text, 'internal'::text]))
        ), glob AS (
         SELECT rels.rel,
            rels.oid,
            rels.relname,
            rels.nspname,
            rels.relkind,
            rels.relispartition,
            rels.relrowsecurity,
            rels.relforcerowsecurity,
            rels.relowner,
            rels.relacl,
            rels.root
           FROM (rels
             JOIN kernel.rls_registry g ON (((g.rel)::oid = (rels.root)::oid)))
          WHERE (g.class = 'global'::text)
        ), exempt AS (
         SELECT e.rel,
            e.conname,
            e.condef_md5,
            e.value_source,
            e.reason,
            e.reviewed_by,
            e.reviewed_at,
            c.oid AS conoid,
            c.conindid,
            md5(pg_get_constraintdef(c.oid)) AS cur_md5
           FROM (kernel.rls_exemptions e
             LEFT JOIN pg_constraint c ON (((c.conrelid = (e.rel)::oid) AND (c.conname = e.conname))))
        ), expected AS (
         SELECT reg.oid,
            t.name,
            t.cmd,
            t.qual,
            t.with_check,
                CASE t.name
                    WHEN 'jobs_all'::text THEN s.jobs
                    WHEN 'vetted_read'::text THEN ((s.vetted_prefix || (reg.nspname)::text))::name
                    WHEN 'vetted_write'::text THEN ((s.vetted_prefix || (reg.nspname)::text))::name
                    WHEN 'backup_read'::text THEN s.backup
                    WHEN 'restore_insert'::text THEN s.restore
                    ELSE s.app_group
                END AS role
           FROM reg,
            s,
            kernel.policy_template t
          WHERE ((t.name = ANY (ARRAY['org_scope'::text, 'backup_read'::text, 'restore_insert'::text])) OR ((t.name = 'lineage_read'::text) AND (reg.class = 'lineage'::text)) OR ((t.name = 'jobs_all'::text) AND reg.jobs_access) OR ((t.name = 'vetted_read'::text) AND reg.vetted_read) OR ((t.name = 'vetted_write'::text) AND reg.vetted_write))
        ), actual AS (
         SELECT p.polrelid AS oid,
            p.polname AS name,
            p.polcmd AS cmd,
            p.polpermissive,
            pg_get_expr(p.polqual, p.polrelid) AS qual,
            pg_get_expr(p.polwithcheck, p.polrelid) AS with_check,
            ( SELECT array_agg(pg_roles.rolname ORDER BY pg_roles.rolname) AS array_agg
                   FROM pg_roles
                  WHERE (pg_roles.oid = ANY (p.polroles))) AS roles
           FROM pg_policy p
        ), fn AS (
         SELECT p.oid,
            ((p.oid)::regprocedure)::text AS name,
            p.proname,
            p.provolatile,
            p.proowner,
            p.prosrc,
            n.nspname,
            g.class,
            g.writer,
            g.scope,
            g.content_ok,
            p.proconfig,
            p.pronargs,
            p.proretset,
            p.prolang,
            p.proargnames,
            p.proargmodes,
            p.prorettype,
            (p.proargtypes)::oid[] AS argtypes,
            COALESCE(p.proacl, acldefault('f'::"char", p.proowner)) AS acl
           FROM ((pg_proc p
             JOIN nsp n ON ((n.oid = p.pronamespace)))
             LEFT JOIN kernel.function_registry g ON (((g.proc)::oid = p.oid)))
          WHERE p.prosecdef
        ), can_exec AS (
         SELECT fn.oid AS fn,
            r.oid AS role
           FROM fn,
            ( SELECT actors.oid
                   FROM actors
                UNION
                 SELECT r2.oid
                   FROM pg_roles r2,
                    s
                  WHERE ((r2.rolname = s.backup) OR (r2.rolname = s.restore))) r
          WHERE (EXISTS ( SELECT 1
                   FROM aclexplode(fn.acl) x(grantor, grantee, privilege_type, is_grantable)
                  WHERE ((x.privilege_type = 'EXECUTE'::text) AND (x.grantee <> (0)::oid) AND (x.grantee IN ( SELECT reach.role
                           FROM reach
                          WHERE (reach.actor = r.oid))))))
        ), cat AS (
         SELECT ('55 SECURITY DEFINER function language is '::text || (l.lanname)::text) AS rule,
            fn.name AS object
           FROM (fn
             JOIN pg_language l ON ((l.oid = fn.prolang)))
          WHERE (l.lanname <> ALL (ARRAY['sql'::name, 'plpgsql'::name]))
        UNION ALL
         SELECT '56 read/write function builds SQL dynamically'::text AS text,
            fn.name
           FROM fn
          WHERE ((fn.class = ANY (ARRAY['read'::text, 'write'::text])) AND (fn.prosrc ~* '\mexecute\M|\mformat\s*\(|\m(query|cursor|table|schema|database)_to_xml\w*'::text))
        UNION ALL
         SELECT '57 write function uses ON CONFLICT'::text AS text,
            fn.name
           FROM fn
          WHERE ((fn.class = 'write'::text) AND (fn.prosrc ~* '\mon\s+conflict\M'::text))
        UNION ALL
         SELECT ('58 SECURITY DEFINER search_path lists '::text || x.e),
            fn.name
           FROM fn,
            LATERAL ( SELECT unnest(string_to_array(substr(cfg.cfg, 13), ', '::text)) AS e
                   FROM unnest(fn.proconfig) cfg(cfg)
                  WHERE (cfg.cfg ~~ 'search_path=%'::text)) x
          WHERE ((x.e <> ALL (ARRAY['pg_catalog'::text, 'pg_temp'::text, 'ext'::text])) AND (x.e <> fn.nspname))
        UNION ALL
         SELECT '59 org-scoped function does not read app.org_id'::text AS text,
            fn.name
           FROM fn
          WHERE ((fn.class = ANY (ARRAY['read'::text, 'write'::text])) AND (fn.scope = ANY (ARRAY['org'::text, 'lineage'::text])) AND (fn.prosrc !~* 'current_setting\s*\(\s*''app\.org_id'''::text))
        UNION ALL
         SELECT ('60 org-scoped function takes an org argument '::text || a.name),
            fn.name
           FROM fn,
            LATERAL unnest(fn.proargnames) WITH ORDINALITY a(name, i)
          WHERE ((fn.class = ANY (ARRAY['read'::text, 'write'::text])) AND (fn.scope = 'org'::text) AND ((a.name ~* '(^|_)(org|organi[sz]ation|workspace|account)(_?id)?($|_)'::text) OR (fn.prosrc ~* (('org_id\s*=\s*'::text || a.name) || '\M'::text))) AND (COALESCE(fn.proargmodes[a.i], 'i'::"char") = ANY (ARRAY['i'::"char", 'b'::"char", 'v'::"char"])))
        UNION ALL
         SELECT '61 global function matches by pattern, not by exact key'::text AS text,
            fn.name
           FROM fn
          WHERE ((fn.scope = 'global'::text) AND (fn.class = ANY (ARRAY['read'::text, 'write'::text])) AND (fn.prosrc ~* '\m(i?like|similar\s+to)\M|\s!?~\*?\s'::text))
        UNION ALL
         SELECT '62 read/write function does not validate its arguments (22023)'::text AS text,
            fn.name
           FROM fn
          WHERE ((fn.class = ANY (ARRAY['read'::text, 'write'::text])) AND (fn.pronargs > 0) AND (fn.prosrc !~ '22023'::text))
        UNION ALL
         SELECT '63 function returns a whole table row'::text AS text,
            fn.name
           FROM fn
          WHERE ((fn.class = ANY (ARRAY['read'::text, 'write'::text])) AND (EXISTS ( SELECT 1
                   FROM (pg_type t
                     JOIN pg_class c ON ((c.oid = t.typrelid)))
                  WHERE ((t.oid = fn.prorettype) AND (c.relkind = ANY (ARRAY['r'::"char", 'p'::"char", 'v'::"char", 'm'::"char", 'f'::"char"]))))))
        UNION ALL
         SELECT '63 function builds json or a row value, which hides whole rows'::text AS text,
            fn.name
           FROM fn
          WHERE ((fn.class = ANY (ARRAY['read'::text, 'write'::text])) AND (NOT COALESCE(fn.content_ok, false)) AND ((fn.prorettype = ANY (ARRAY[('json'::regtype)::oid, ('jsonb'::regtype)::oid])) OR (fn.prosrc ~* '\m(to_jsonb?|row_to_json|json_agg|jsonb_agg|json_build_object|jsonb_build_object|row)\M\s*\('::text)))
        UNION ALL
         SELECT ('64 function returns a content column '::text || a.name),
            fn.name
           FROM fn,
            LATERAL unnest(fn.proargnames) WITH ORDINALITY a(name, i)
          WHERE ((fn.class = ANY (ARRAY['read'::text, 'write'::text])) AND (NOT COALESCE(fn.content_ok, false)) AND (fn.proargmodes[a.i] = ANY (ARRAY['t'::"char", 'o'::"char"])) AND (a.name ~* '^(body|content|text|html|markdown|payload|data|raw|blob|document)$'::text))
        UNION ALL
         SELECT '65 set-returning read function has no LIMIT'::text AS text,
            fn.name
           FROM fn
          WHERE ((fn.class = 'read'::text) AND fn.proretset AND (fn.prosrc !~* '\mlimit\M'::text))
        UNION ALL
         SELECT ('66 read-class function executable by '::text || ((x.role)::regrole)::text),
            fn.name
           FROM (fn
             JOIN can_exec x ON ((x.fn = fn.oid))),
            s
          WHERE ((fn.class = 'read'::text) AND (fn.scope <> 'helper'::text) AND (x.role IN ( SELECT pg_roles.oid
                   FROM pg_roles
                  WHERE (pg_roles.rolname = ANY (ARRAY[s.jobs, s.backup, s.restore])))))
        UNION ALL
         SELECT '67 function body can raise on row data'::text AS text,
            fn.name
           FROM fn
          WHERE ((fn.class = ANY (ARRAY['read'::text, 'write'::text])) AND (fn.prosrc ~* '::\s*(?!(text|varchar|name)\M)|\mcast\s*\(|\mto_(date|timestamp|number|char)\M|\mjsonb?_path|\m(int[248]|float[48]|numeric|bool|date|timestamptz?|uuid|jsonb?)\s*\(|[\w)\]]\s*/\s*[\w(]|\w\s*%\s*[\w(]'::text))
        UNION ALL
         SELECT (('68 kernel.'::text || f.name) || ' is missing or changed in shape'::text),
            ('kernel.'::text || f.name)
           FROM ( VALUES ('org_parent_ids'::text,'helper'::text), ('org_lineage'::text,'lineage'::text)) f(name, scope)
          WHERE (NOT (EXISTS ( SELECT 1
                   FROM fn
                  WHERE ((fn.nspname = 'kernel'::name) AND (fn.proname = f.name) AND (array_to_string(fn.argtypes, ','::text) = (('text'::regtype)::oid)::text) AND (fn.prorettype = ('text[]'::regtype)::oid) AND (fn.provolatile = 's'::"char") AND (fn.class = 'read'::text) AND (fn.scope = f.scope) AND (fn.pronargs = 1)))))
        UNION ALL
         SELECT '70 built-in function is SECURITY DEFINER or has settings'::text AS text,
            ((p.oid)::regprocedure)::text AS oid
           FROM (pg_proc p
             JOIN pg_namespace n ON ((n.oid = p.pronamespace)))
          WHERE ((n.nspname = ANY (ARRAY['pg_catalog'::name, 'information_schema'::name])) AND (p.oid < (16384)::oid) AND (p.prosecdef OR (p.proconfig IS NOT NULL)))
        UNION ALL
         SELECT ('72 helper function executable by '::text || ((x.role)::regrole)::text),
            fn.name
           FROM (fn
             JOIN can_exec x ON ((x.fn = fn.oid)))
          WHERE ((fn.scope = 'helper'::text) AND (x.role <> fn.proowner))
        UNION ALL
         SELECT ('72 helper function executable by '::text || ((a.grantee)::regrole)::text),
            fn.name
           FROM fn,
            LATERAL aclexplode(fn.acl) a(grantor, grantee, privilege_type, is_grantable)
          WHERE ((fn.scope = 'helper'::text) AND (a.privilege_type = 'EXECUTE'::text) AND (a.grantee <> fn.proowner) AND (a.grantee IN ( SELECT vetted_roles.oid
                   FROM vetted_roles)))
        UNION ALL
         SELECT (((('73 vetted role '::text || ((a.grantee)::regrole)::text) || ' holds '::text) || a.privilege_type) || ' on a table that did not opt in'::text),
            ((c.oid)::regclass)::text AS oid
           FROM ((pg_class c
             JOIN nsp n ON ((n.oid = c.relnamespace)))
             JOIN kernel.rls_registry r ON (((r.rel)::oid = c.oid))),
            LATERAL aclexplode(c.relacl) a(grantor, grantee, privilege_type, is_grantable)
          WHERE ((a.grantee IN ( SELECT vetted_roles.oid
                   FROM vetted_roles)) AND (a.privilege_type = ANY (ARRAY['SELECT'::text, 'INSERT'::text, 'UPDATE'::text])) AND (NOT (r.vetted_read OR r.vetted_write)) AND (NOT ((a.privilege_type = 'SELECT'::text) AND (c.oid IN ( SELECT vetted_extension_reads.rel
                   FROM kernel.vetted_extension_reads)) AND (a.grantee = (to_regrole((( SELECT settings.vetted_prefix
                   FROM kernel.settings) || 'kernel'::text)))::oid))))
        UNION ALL
         SELECT '74 function raises other than a constant EXCEPTION with an SQLSTATE'::text AS text,
            fn.name
           FROM fn
          WHERE ((fn.class = ANY (ARRAY['read'::text, 'write'::text, 'jobs'::text])) AND ((EXISTS ( SELECT 1
                   FROM regexp_matches(fn.prosrc, '\mraise\M[^;]*'::text, 'gi'::text) m(x)
                  WHERE (m.x[1] !~* '^raise\s+exception\s+''[^''%]*''\s+using\s+errcode\s*=\s*''[0-9A-Z]{5}''\s*$'::text))) OR (fn.prosrc ~* '\massert\M'::text)))
        UNION ALL
         SELECT '75 vetted-owner read outside a declared extension schema'::text AS text,
            (v.rel)::text AS rel
           FROM kernel.vetted_extension_reads v
          WHERE (NOT (EXISTS ( SELECT 1
                   FROM ((kernel.extension_schemas e
                     JOIN pg_class c ON ((c.oid = (v.rel)::oid)))
                     JOIN pg_namespace n ON (((n.oid = c.relnamespace) AND (n.nspname = e.nsp)))))))
        )
 SELECT rule,
    object
   FROM cat;
CREATE OR REPLACE VIEW kernel.person_violations WITH (security_invoker = true) AS
WITH RECURSIVE s AS (
         SELECT settings.one,
            settings.owner,
            settings.migrate,
            settings.app_group,
            settings.jobs,
            settings.role_prefix,
            settings.vetted_prefix,
            settings.backup,
            settings.restore,
            settings.ro,
            settings.services,
            settings.admins,
            settings.extensions,
            settings.org_id_pattern
           FROM kernel.settings
        ), nsp AS (
         SELECT n.oid,
            n.nspname
           FROM pg_namespace n
          WHERE ((n.nspname <> ALL (ARRAY['pg_catalog'::name, 'information_schema'::name, 'pg_toast'::name])) AND (n.nspname !~~ 'pg_temp%'::text) AND (n.nspname !~~ 'pg_toast_temp%'::text))
        ), admin_roles AS (
         SELECT r.oid
           FROM pg_roles r,
            s
          WHERE (r.rolname = ANY (s.admins))
        ), trusted AS (
         SELECT r.oid
           FROM pg_roles r,
            s
          WHERE ((r.rolname = s.owner) OR (r.rolname = s.migrate) OR (r.rolname = s.backup) OR (r.rolname = s.restore) OR (r.rolname ~~ (s.vetted_prefix || '%'::text)))
        ), rall AS (
         SELECT r.oid
           FROM pg_roles r
          WHERE ((NOT r.rolsuper) AND (r.rolname !~~ 'pg\_%'::text) AND (r.oid IS DISTINCT FROM kernel.bootstrap_admin()))
        ), actors AS (
         SELECT rall.oid
           FROM rall
          WHERE ((NOT (rall.oid IN ( SELECT trusted.oid
                   FROM trusted))) AND (NOT (rall.oid IN ( SELECT admin_roles.oid
                   FROM admin_roles))))
        ), reach AS (
         WITH RECURSIVE t(actor, role) AS (
                 SELECT a.oid,
                    a.oid
                   FROM rall a
                UNION
                 SELECT t_1.actor,
                    m.roleid
                   FROM (t t_1
                     JOIN pg_auth_members m ON ((m.member = t_1.role)))
                )
         SELECT t.actor,
            t.role
           FROM t
        ), reach_roles AS (
         SELECT DISTINCT reach.role AS oid
           FROM reach
          WHERE (reach.actor IN ( SELECT actors.oid
                   FROM actors))
        ), vetted_roles AS (
         SELECT r.oid,
            r.rolname
           FROM pg_roles r,
            s
          WHERE (r.rolname ~~ (s.vetted_prefix || '%'::text))
        ), glob AS (
         SELECT c.oid,
            c.relname,
            n.nspname,
            g.person,
            g.vetted_delete,
            g.vetted_write
           FROM ((kernel.rls_registry g
             JOIN pg_class c ON ((c.oid = (g.rel)::oid)))
             JOIN pg_namespace n ON ((n.oid = c.relnamespace)))
          WHERE (g.class = 'global'::text)
        ), person_views AS (
         SELECT w.ev_class AS oid,
            d.refobjid AS base
           FROM (pg_depend d
             JOIN pg_rewrite w ON ((w.oid = d.objid)))
          WHERE ((d.classid = ('pg_rewrite'::regclass)::oid) AND (d.refclassid = ('pg_class'::regclass)::oid) AND (d.refobjid IN ( SELECT glob.oid
                   FROM glob
                  WHERE glob.person)) AND (w.ev_class <> d.refobjid))
        UNION
         SELECT w.ev_class,
            pv.base
           FROM ((person_views pv
             JOIN pg_depend d ON (((d.refobjid = pv.oid) AND (d.classid = ('pg_rewrite'::regclass)::oid) AND (d.refclassid = ('pg_class'::regclass)::oid))))
             JOIN pg_rewrite w ON ((w.oid = d.objid)))
          WHERE (w.ev_class <> pv.oid)
        ), inv AS (
         SELECT p.oid,
            ((p.oid)::regprocedure)::text AS name,
            kernel.src_text(p.prosrc) AS src,
            p.prosrc,
            COALESCE(p.proacl, acldefault('f'::"char", p.proowner)) AS acl
           FROM (pg_proc p
             JOIN nsp n ON ((n.oid = p.pronamespace)))
          WHERE ((NOT p.prosecdef) AND (p.prokind = ANY (ARRAY['f'::"char", 'p'::"char"])) AND (p.prolang IN ( SELECT pg_language.oid
                   FROM pg_language
                  WHERE (pg_language.lanname = ANY (ARRAY['sql'::name, 'plpgsql'::name])))))
        UNION
         SELECT p.oid,
            ((p.oid)::regprocedure)::text AS oid,
            kernel.src_text(p.prosrc) AS src_text,
            p.prosrc,
            acldefault('f'::"char", p.proowner) AS acldefault
           FROM (((pg_trigger t
             JOIN pg_proc p ON ((p.oid = t.tgfoid)))
             JOIN pg_class c ON ((c.oid = t.tgrelid)))
             JOIN nsp n ON ((n.oid = c.relnamespace)))
          WHERE ((NOT t.tgisinternal) AND (NOT p.prosecdef) AND (p.prolang IN ( SELECT pg_language.oid
                   FROM pg_language
                  WHERE (pg_language.lanname = ANY (ARRAY['sql'::name, 'plpgsql'::name])))))
        ), fn AS (
         SELECT p.oid,
            ((p.oid)::regprocedure)::text AS name,
            p.proname,
            p.proowner,
            p.prosrc,
            kernel.src_text(p.prosrc) AS src,
            kernel.src_text(p.prosrc, true) AS srck,
            n.nspname,
            g.class,
            g.scope,
            g.person_effect,
            g.dedupe_rel,
            p.proconfig,
            p.pronargs,
            p.proretset,
            p.prorettype,
            p.proargnames,
            p.proargmodes,
            COALESCE(p.proacl, acldefault('f'::"char", p.proowner)) AS acl
           FROM ((pg_proc p
             JOIN nsp n ON ((n.oid = p.pronamespace)))
             LEFT JOIN kernel.function_registry g ON (((g.proc)::oid = p.oid)))
          WHERE p.prosecdef
        ), chk AS (
         SELECT fn.name,
            fn.src,
            fn.prosrc
           FROM fn
        UNION ALL
         SELECT i.name,
            i.src,
            i.prosrc
           FROM inv i
          WHERE (EXISTS ( SELECT 1
                   FROM aclexplode(i.acl) x(grantor, grantee, privilege_type, is_grantable)
                  WHERE ((x.privilege_type = 'EXECUTE'::text) AND ((x.grantee = (0)::oid) OR (x.grantee IN ( SELECT vetted_roles.oid
                           FROM vetted_roles))))))
        ), can_exec AS (
         SELECT fn.oid AS fn,
            r.oid AS role
           FROM fn,
            ( SELECT actors.oid
                   FROM actors
                UNION
                 SELECT r2.oid
                   FROM pg_roles r2,
                    s
                  WHERE ((r2.rolname = s.backup) OR (r2.rolname = s.restore))) r
          WHERE (EXISTS ( SELECT 1
                   FROM aclexplode(fn.acl) x(grantor, grantee, privilege_type, is_grantable)
                  WHERE ((x.privilege_type = 'EXECUTE'::text) AND (x.grantee <> (0)::oid) AND (x.grantee IN ( SELECT reach.role
                           FROM reach
                          WHERE (reach.actor = r.oid))))))
        ), cat AS (
         SELECT '76 person table has an org_id column'::text AS rule,
            ((glob.oid)::regclass)::text AS object
           FROM glob
          WHERE (glob.person AND (EXISTS ( SELECT 1
                   FROM pg_attribute a
                  WHERE ((a.attrelid = glob.oid) AND (a.attname = 'org_id'::name) AND (NOT a.attisdropped)))))
        UNION ALL
         SELECT '77 person function is not global scope'::text AS text,
            fn.name
           FROM fn
          WHERE ((fn.person_effect IS NOT NULL) AND (fn.scope IS DISTINCT FROM 'global'::text))
        UNION ALL
         SELECT '78 person function does not read app.user_id'::text AS text,
            fn.name
           FROM fn
          WHERE ((fn.person_effect = ANY (ARRAY['read'::text, 'self'::text, 'admin'::text])) AND (fn.srck !~ 'app\.user_id'::text))
        UNION ALL
         SELECT '79 person function does not resolve the acting principal'::text AS text,
            fn.name
           FROM fn
          WHERE (((fn.person_effect = ANY (ARRAY['read'::text, 'self'::text])) AND (fn.src !~ 'v_actor'::text)) OR ((fn.person_effect = 'admin'::text) AND (fn.src !~ 'v_admin'::text)))
        UNION ALL
         SELECT '80 person write does not take the per-email advisory lock'::text AS text,
            fn.name
           FROM fn
          WHERE ((fn.person_effect = ANY (ARRAY['self'::text, 'admin'::text, 'preorg'::text])) AND ((fn.src !~* (('pg_advisory_xact_lock\s*\(\s*7350\s*,\s*hashtext\s*\(\s*('::text || COALESCE(array_to_string(fn.proargnames, '|'::text), '-'::text)) || ')\s*\)\s*\)'::text)) OR (fn.src ~* '\m(insert\s+into|update|delete\s+from)\M[\s\S]*pg_advisory_xact_lock'::text)))
        UNION ALL
         SELECT '81 person write has no membership proof'::text AS text,
            fn.name
           FROM fn
          WHERE ((fn.person_effect = ANY (ARRAY['self'::text, 'admin'::text])) AND (fn.src !~ 'v_member'::text))
        UNION ALL
         SELECT '82 admin effect writes no audit row'::text AS text,
            fn.name
           FROM fn
          WHERE ((fn.person_effect = 'admin'::text) AND (fn.src !~* 'insert\s+into\s+\S+\s*\(\s*org_id,\s*actor_id,\s*target_email,\s*effect\s*\)'::text))
        UNION ALL
         SELECT '83 admin effect is not in the closed list'::text AS text,
            fn.name
           FROM fn
          WHERE ((fn.person_effect = 'admin'::text) AND (fn.srck !~ '''(unblock|role_change|admin_signout|clear_if_last)'''::text))
        UNION ALL
         SELECT '84 pre-org person function returns a value'::text AS text,
            fn.name
           FROM fn
          WHERE ((fn.person_effect = 'preorg'::text) AND (fn.prorettype <> ('void'::regtype)::oid) AND (fn.proname <> 'consume_otp'::name))
        UNION ALL
         SELECT '84 pre-org person function runs with an org set'::text AS text,
            fn.name
           FROM fn
          WHERE ((fn.person_effect = 'preorg'::text) AND (fn.src !~ 'v_org IS NOT NULL'::text))
        UNION ALL
         SELECT ('85 person function takes a caller-chosen limit '::text || a.name),
            fn.name
           FROM fn,
            LATERAL unnest(fn.proargnames) WITH ORDINALITY a(name, i)
          WHERE ((fn.person_effect IS NOT NULL) AND (COALESCE(fn.proargmodes[a.i], 'i'::"char") = ANY (ARRAY['i'::"char", 'b'::"char", 'v'::"char"])) AND (a.name ~* '(^|_)(limit|threshold|cap|max|min|count|size|top|offset|page)($|_)'::text))
        UNION ALL
         SELECT '85 person read function has a LIMIT that is not a constant'::text AS text,
            fn.name
           FROM fn
          WHERE ((fn.person_effect = 'read'::text) AND fn.proretset AND (( SELECT count(*) AS count
                   FROM regexp_matches(fn.src, '\mlimit\M'::text, 'gi'::text) regexp_matches(regexp_matches)) <> ( SELECT count(*) AS count
                   FROM regexp_matches(fn.src, '\mlimit\s+\d+\s*(;|\)|offset\M)'::text, 'gi'::text) regexp_matches(regexp_matches))))
        UNION ALL
         SELECT (('31 jobs role can execute a '::text || COALESCE(fn.class, 'unregistered'::text)) || '-class function'::text),
            fn.name
           FROM (fn
             JOIN can_exec x ON ((x.fn = fn.oid))),
            s
          WHERE ((x.role = (to_regrole((s.jobs)::text))::oid) AND (x.role <> fn.proowner) AND (fn.class IS DISTINCT FROM 'jobs'::text) AND (fn.class IS DISTINCT FROM 'prologue'::text))
        UNION ALL
         SELECT '31 function of a global table''s vetted owner writes that table and is neither jobs nor write class'::text AS text,
            fn.name
           FROM fn,
            glob,
            s
          WHERE ((fn.proowner = (to_regrole((s.vetted_prefix || (glob.nspname)::text)))::oid) AND (fn.class IS DISTINCT FROM 'jobs'::text) AND (fn.class IS DISTINCT FROM 'write'::text) AND (fn.src ~* (((('\m(insert\s+into|update|delete\s+from)\s+('::text || (glob.nspname)::text) || '\.)?'::text) || (glob.relname)::text) || '\M'::text)))
        UNION ALL
         SELECT '31 jobs-class function that writes a person table or deletes is not named import_* (writes) or sweep_* (deletes)'::text AS text,
            fn.name
           FROM fn
          WHERE ((fn.class = 'jobs'::text) AND (NOT (( SELECT bool_or((fn.src ~* (((('\m(insert into|update)\s+('::text || (g.nspname)::text) || '\.)?'::text) || (g.relname)::text) || '\M'::text))) AS bool_or
                   FROM glob g
                  WHERE g.person) IS DISTINCT FROM true)) AND (NOT ((fn.proname ~ '^import_'::text) AND (fn.src !~* '\m(delete from|truncate)\M'::text))))
        UNION ALL
         SELECT '31 jobs-class function that deletes is not a sweep_* function'::text AS text,
            fn.name
           FROM fn
          WHERE ((fn.class = 'jobs'::text) AND (fn.src ~* '\m(delete from|truncate)\M'::text) AND (fn.proname !~ '^sweep_'::text))
        UNION ALL
         SELECT '31 sweep function writes a person table'::text AS text,
            fn.name
           FROM fn
          WHERE ((fn.class = 'jobs'::text) AND (fn.proname ~ '^sweep_'::text) AND (NOT (( SELECT bool_or((fn.src ~* (((('\m(insert into|update)\s+('::text || (g.nspname)::text) || '\.)?'::text) || (g.relname)::text) || '\M'::text))) AS bool_or
                   FROM glob g
                  WHERE g.person) IS DISTINCT FROM true)))
        UNION ALL
         SELECT '31 import function does not refuse unless a job is running'::text AS text,
            fn.name
           FROM fn
          WHERE ((fn.class = 'jobs'::text) AND (fn.proname ~ '^import_'::text) AND (fn.src !~* 'platform\.jobs'::text))
        UNION ALL
         SELECT '31 import function deletes'::text AS text,
            fn.name
           FROM fn
          WHERE ((fn.class = 'jobs'::text) AND (fn.proname ~ '^import_'::text) AND (fn.src ~* '\m(delete\s+from|truncate)\M'::text))
        UNION ALL
         SELECT '31 sweep function takes arguments or has no fixed expiry predicate'::text AS text,
            fn.name
           FROM fn
          WHERE ((fn.class = 'jobs'::text) AND (fn.proname ~ '^sweep_'::text) AND ((fn.pronargs > 0) OR (fn.src ~* '\mdelete\s+from\M[\s\S]*\mdelete\s+from\M'::text) OR (fn.src !~* '\mdelete\s+from\s+[a-z_][a-z0-9_.]*\s+where\s+[a-z_][a-z0-9_.]*\s*<=?\s*(now\s*\(\s*\)|current_timestamp)(\s*-\s*interval\s+'''')?\s*;'::text) OR (( SELECT count(*) AS count
                   FROM regexp_matches(fn.src, '\minterval\M'::text, 'gi'::text) regexp_matches(regexp_matches)) <> ( SELECT count(*) AS count
                   FROM regexp_matches(fn.srck, '\minterval\s+''[0-9]+\s+(second|minute|hour|day|week|month|year)s?'''::text, 'gi'::text) regexp_matches(regexp_matches))) OR (fn.src ~* '\m(insert\s+into|update|merge)\M'::text)))
        UNION ALL
         SELECT '88 read or write function deletes'::text AS text,
            fn.name
           FROM fn
          WHERE ((fn.class = ANY (ARRAY['read'::text, 'write'::text])) AND (fn.src ~* '\m(delete\s+from|truncate)\M'::text))
        UNION ALL
         SELECT (((('89 vetted role '::text || ((a.grantee)::regrole)::text) || ' holds '::text) || a.privilege_type) || ' on a table without vetted_delete'::text),
            ((c.oid)::regclass)::text AS oid
           FROM ((pg_class c
             JOIN nsp n ON ((n.oid = c.relnamespace)))
             JOIN kernel.rls_registry r ON (((r.rel)::oid = c.oid))),
            LATERAL aclexplode(c.relacl) a(grantor, grantee, privilege_type, is_grantable)
          WHERE ((a.grantee IN ( SELECT vetted_roles.oid
                   FROM vetted_roles)) AND (a.privilege_type = ANY (ARRAY['DELETE'::text, 'TRUNCATE'::text])) AND (NOT (r.vetted_delete AND (r.class = 'global'::text))))
        UNION ALL
         SELECT '90 dedupe table is not an external-issuer exemption'::text AS text,
            fn.name
           FROM fn
          WHERE ((fn.dedupe_rel IS NOT NULL) AND (NOT (EXISTS ( SELECT 1
                   FROM kernel.rls_exemptions e
                  WHERE (((e.rel)::oid = (fn.dedupe_rel)::oid) AND (e.value_source = 'external_issuer'::text))))))
        UNION ALL
         SELECT '90 dedupe table is not a global table without org data'::text AS text,
            fn.name
           FROM fn
          WHERE ((fn.dedupe_rel IS NOT NULL) AND ((NOT (EXISTS ( SELECT 1
                   FROM kernel.rls_registry g
                  WHERE (((g.rel)::oid = (fn.dedupe_rel)::oid) AND (g.class = 'global'::text))))) OR (EXISTS ( SELECT 1
                   FROM pg_attribute a
                  WHERE ((a.attrelid = (fn.dedupe_rel)::oid) AND (a.attname = 'org_id'::name) AND (NOT a.attisdropped))))))
        UNION ALL
         SELECT '90 dedupe function returns more than the answer'::text AS text,
            fn.name
           FROM fn
          WHERE ((fn.dedupe_rel IS NOT NULL) AND ((fn.prorettype <> ('boolean'::regtype)::oid) OR fn.proretset))
        UNION ALL
         SELECT '90 dedupe function writes a table other than its own'::text AS text,
            fn.name
           FROM fn,
            LATERAL regexp_matches(fn.src, '\m(?:insert\s+into|update|delete\s+from)\s+([a-z_][a-z0-9_.]*)'::text, 'gi'::text) m(g)
          WHERE ((fn.dedupe_rel IS NOT NULL) AND (m.g[1] !~* (((('^('::text || (fn.nspname)::text) || '\.)?'::text) || (( SELECT pg_class.relname
                   FROM pg_class
                  WHERE (pg_class.oid = (fn.dedupe_rel)::oid)))::text) || '$'::text)))
        UNION ALL
         SELECT '90 dedupe function updates on conflict'::text AS text,
            fn.name
           FROM fn
          WHERE ((fn.dedupe_rel IS NOT NULL) AND (fn.src ~* 'do\s+update'::text))
        UNION ALL
         SELECT '32 prologue function is not SECURITY DEFINER'::text AS text,
            ((p.oid)::regprocedure)::text AS oid
           FROM (pg_proc p
             JOIN kernel.function_registry g ON (((g.proc)::oid = p.oid)))
          WHERE ((g.class = 'prologue'::text) AND (NOT p.prosecdef))
        UNION ALL
         SELECT '32 prologue function takes no shared advisory barrier'::text AS text,
            ((p.oid)::regprocedure)::text AS oid
           FROM (pg_proc p
             JOIN kernel.function_registry g ON (((g.proc)::oid = p.oid)))
          WHERE ((g.class = 'prologue'::text) AND (kernel.src_text(p.prosrc) !~* 'pg_try_advisory_xact_lock_shared\s*\(\s*\d+\s*,\s*\d+\s*\)'::text))
        UNION ALL
         SELECT '32 prologue function does not refuse with 25006'::text AS text,
            ((p.oid)::regprocedure)::text AS oid
           FROM (pg_proc p
             JOIN kernel.function_registry g ON (((g.proc)::oid = p.oid)))
          WHERE ((g.class = 'prologue'::text) AND (kernel.src_text(p.prosrc, true) !~ '25006'::text))
        UNION ALL
         SELECT '32 application role can read a table the fence reads'::text AS text,
            (v.rel)::text AS rel
           FROM kernel.vetted_extension_reads v,
            pg_class c,
            LATERAL aclexplode(c.relacl) a(grantor, grantee, privilege_type, is_grantable)
          WHERE ((c.oid = (v.rel)::oid) AND ((a.grantee = (0)::oid) OR (a.grantee IN ( SELECT reach_roles.oid
                   FROM reach_roles))))
        UNION ALL
         SELECT ('94 function outside the person functions names the person table '::text || (glob.relname)::text),
            fn.name
           FROM fn,
            glob
          WHERE (glob.person AND (fn.person_effect IS NULL) AND (fn.class IS DISTINCT FROM 'jobs'::text) AND (fn.src ~* (((('\m('::text || (glob.nspname)::text) || '\.)?'::text) || (glob.relname)::text) || '\M'::text)))
        UNION ALL
         SELECT ('95 global or jobs function writes the org-scoped table '::text || (c.relname)::text),
            fn.name
           FROM fn,
            (kernel.rls_registry g
             JOIN pg_class c ON ((c.oid = (g.rel)::oid)))
          WHERE ((g.class = ANY (ARRAY['org'::text, 'lineage'::text])) AND ((fn.scope = 'global'::text) OR (fn.class = 'jobs'::text)) AND (fn.person_effect IS DISTINCT FROM 'self'::text) AND (fn.person_effect IS DISTINCT FROM 'admin'::text) AND (fn.src ~* (('\m(insert\s+into|update|delete\s+from)\s+([a-z_][a-z0-9_]*\.)?'::text || (c.relname)::text) || '\M'::text)))
        UNION ALL
         SELECT (('96 application role or PUBLIC holds '::text || a.privilege_type) || ' on the jobs table'::text),
            ((c.oid)::regclass)::text AS oid
           FROM (pg_class c
             JOIN pg_namespace n ON (((n.oid = c.relnamespace) AND (n.nspname = 'platform'::name) AND (c.relname = 'jobs'::name)))),
            LATERAL aclexplode(c.relacl) a(grantor, grantee, privilege_type, is_grantable)
          WHERE ((a.grantee = (0)::oid) OR (a.grantee IN ( SELECT reach_roles.oid
                   FROM reach_roles)))
        UNION ALL
         SELECT '97 routine owned by a vetted role is not SECURITY DEFINER'::text AS text,
            ((p.oid)::regprocedure)::text AS oid
           FROM (pg_proc p
             JOIN pg_namespace n ON ((n.oid = p.pronamespace)))
          WHERE ((p.proowner IN ( SELECT vetted_roles.oid
                   FROM vetted_roles)) AND (NOT p.prosecdef) AND (p.prokind = ANY (ARRAY['f'::"char", 'p'::"char"])))
        UNION ALL
         SELECT '97 invoker routine that writes can be called by a vetted role or PUBLIC'::text AS text,
            i.name
           FROM inv i
          WHERE ((i.src ~* '\m(insert into|update [a-z_][a-z0-9_.]*( as)?( [a-z_][a-z0-9_]*)? set|delete from|truncate|merge|execute|call|copy)\M'::text) AND (NOT (EXISTS ( SELECT 1
                   FROM kernel.trigger_exemptions te
                  WHERE (((te.proc)::oid = i.oid) AND (te.body_md5 = md5(i.prosrc)))))) AND (EXISTS ( SELECT 1
                   FROM aclexplode(i.acl) x(grantor, grantee, privilege_type, is_grantable)
                  WHERE ((x.privilege_type = 'EXECUTE'::text) AND ((x.grantee = (0)::oid) OR (x.grantee IN ( SELECT vetted_roles.oid
                           FROM vetted_roles)))))))
        UNION ALL
         SELECT ('97 invoker routine that can be called by a vetted role or PUBLIC names the person table '::text || (g.relname)::text),
            i.name
           FROM inv i,
            glob g
          WHERE (g.person AND (i.src ~* (((('\m('::text || (g.nspname)::text) || '\.)?'::text) || (g.relname)::text) || '\M'::text)) AND (EXISTS ( SELECT 1
                   FROM aclexplode(i.acl) x(grantor, grantee, privilege_type, is_grantable)
                  WHERE ((x.privilege_type = 'EXECUTE'::text) AND ((x.grantee = (0)::oid) OR (x.grantee IN ( SELECT vetted_roles.oid
                           FROM vetted_roles)))))))
        UNION ALL
         SELECT ('97 invoker routine that can be called by a vetted role or PUBLIC names the kernel table '::text || (c.relname)::text),
            i.name
           FROM inv i,
            (pg_class c
             JOIN pg_namespace n ON ((n.oid = c.relnamespace)))
          WHERE ((n.nspname = 'kernel'::name) AND (c.relkind = ANY (ARRAY['r'::"char", 'p'::"char", 'v'::"char"])) AND (i.src ~* (('\mkernel\.'::text || (c.relname)::text) || '\M'::text)) AND (EXISTS ( SELECT 1
                   FROM aclexplode(i.acl) x(grantor, grantee, privilege_type, is_grantable)
                  WHERE ((x.privilege_type = 'EXECUTE'::text) AND ((x.grantee = (0)::oid) OR (x.grantee IN ( SELECT vetted_roles.oid
                           FROM vetted_roles)))))))
        UNION ALL
         SELECT ('99 SECURITY DEFINER function sets '::text || split_part(c.c, '='::text, 1)),
            fn.name
           FROM fn,
            LATERAL unnest(fn.proconfig) c(c)
          WHERE (c.c !~ '^(search_path|plan_cache_mode|standard_conforming_strings)='::text)
        UNION ALL
         SELECT '99 SECURITY DEFINER function does not set standard_conforming_strings = on'::text AS text,
            fn.name
           FROM fn
          WHERE (NOT ('standard_conforming_strings=on'::text = ANY (COALESCE(fn.proconfig, '{}'::text[]))))
        UNION ALL
         SELECT ('98 function body uses a construct the checks cannot classify: '::text || m.g[1]),
            chk.name
           FROM chk,
            LATERAL regexp_matches(chk.src, '(\m(?:merge|only|fetch|offset|call)\M|"|\mu&|\m(?:update|from|join|into)\s+[a-z_][a-z0-9_.]*\*)'::text, 'gi'::text) m(g)
        UNION ALL
         SELECT '98 function body uses a construct the checks cannot classify: dollar quote'::text AS text,
            chk.name
           FROM chk
          WHERE (chk.prosrc ~ '\$([^\x01-\x7f]|[A-Za-z_$])'::text)
        UNION ALL
         SELECT '98 function body uses a construct the checks cannot classify: E string'::text AS text,
            chk.name
           FROM chk
          WHERE (chk.prosrc ~* '(^|[^A-Za-z0-9_''.$])e'''::text)
        UNION ALL
         SELECT ('94 view over the person table '::text || (glob.relname)::text),
            ((pv.oid)::regclass)::text AS oid
           FROM (person_views pv
             JOIN glob ON ((glob.oid = pv.base)))
          WHERE glob.person
        UNION ALL
         SELECT '56 read/write/jobs function builds SQL dynamically'::text AS text,
            fn.name
           FROM fn
          WHERE ((fn.class = ANY (ARRAY['read'::text, 'write'::text, 'jobs'::text])) AND (fn.src ~* '\mexecute\M|\mformat\s*\(|\m(query|cursor|table|schema|database)_to_xml\w*'::text))
        UNION ALL
         SELECT '57 write function uses ON CONFLICT'::text AS text,
            fn.name
           FROM fn
          WHERE ((fn.class = 'write'::text) AND (fn.src ~* '\mon conflict\M'::text))
        UNION ALL
         SELECT '36 read-class function body has DML or dynamic SQL'::text AS text,
            fn.name
           FROM fn
          WHERE ((fn.class = 'read'::text) AND (fn.src ~* '\m(insert into|delete from|truncate|execute|copy|merge)\M|\mupdate [a-z_][a-z0-9_.]*( as)?( [a-z_][a-z0-9_]*)? set\M'::text))
        UNION ALL
         SELECT '61 global function matches by pattern, not by exact key'::text AS text,
            fn.name
           FROM fn
          WHERE ((fn.scope = 'global'::text) AND (fn.class = ANY (ARRAY['read'::text, 'write'::text])) AND (fn.src ~* '\m(i?like|similar to)\M|\s!?~\*?\s'::text))
        UNION ALL
         SELECT '91 text column takes its default from a shared sequence'::text AS text,
            ((((c.oid)::regclass)::text || '.'::text) || (a.attname)::text)
           FROM (((pg_attrdef d
             JOIN pg_attribute a ON (((a.attrelid = d.adrelid) AND (a.attnum = d.adnum))))
             JOIN pg_class c ON ((c.oid = d.adrelid)))
             JOIN nsp n ON ((n.oid = c.relnamespace)))
          WHERE ((a.atttypid = ANY (ARRAY[('text'::regtype)::oid, ('character varying'::regtype)::oid, ('character'::regtype)::oid])) AND (NOT a.attisdropped) AND (EXISTS ( SELECT 1
                   FROM (pg_depend dp
                     JOIN pg_class sq ON (((sq.oid = dp.refobjid) AND (sq.relkind = 'S'::"char"))))
                  WHERE ((dp.classid = ('pg_attrdef'::regclass)::oid) AND (dp.objid = d.oid) AND (dp.refclassid = ('pg_class'::regclass)::oid)))) AND (NOT (EXISTS ( SELECT 1
                   FROM kernel.sequence_waivers w
                  WHERE (((w.rel)::oid = c.oid) AND (w.attname = a.attname) AND (EXISTS ( SELECT 1
                           FROM pg_depend dp
                          WHERE ((dp.classid = ('pg_attrdef'::regclass)::oid) AND (dp.objid = d.oid) AND (dp.refobjid = (w.seq)::oid)))))))))
        UNION ALL
         SELECT '92 waived sequence no longer feeds its column'::text AS text,
            (w.seq)::text AS seq
           FROM kernel.sequence_waivers w
          WHERE (NOT (EXISTS ( SELECT 1
                   FROM ((pg_attrdef d
                     JOIN pg_attribute a ON (((a.attrelid = d.adrelid) AND (a.attnum = d.adnum))))
                     JOIN pg_depend dp ON (((dp.classid = ('pg_attrdef'::regclass)::oid) AND (dp.objid = d.oid) AND (dp.refobjid = (w.seq)::oid))))
                  WHERE ((d.adrelid = (w.rel)::oid) AND (a.attname = w.attname)))))
        UNION ALL
         SELECT '92 waived sequence feeds another column'::text AS text,
            (w.seq)::text AS seq
           FROM kernel.sequence_waivers w
          WHERE (EXISTS ( SELECT 1
                   FROM (pg_depend dp
                     JOIN pg_attrdef d ON (((d.oid = dp.objid) AND (dp.classid = ('pg_attrdef'::regclass)::oid))))
                  WHERE ((dp.refobjid = (w.seq)::oid) AND (NOT ((d.adrelid = (w.rel)::oid) AND (d.adnum = ( SELECT pg_attribute.attnum
                           FROM pg_attribute
                          WHERE ((pg_attribute.attrelid = (w.rel)::oid) AND (pg_attribute.attname = w.attname)))))))))
        UNION ALL
         SELECT '93 session-level advisory lock'::text AS text,
            fn.name
           FROM fn
          WHERE (fn.src ~* 'pg_(try_)?advisory_(lock|unlock)\w*\s*\('::text)
        UNION ALL
         SELECT '93 advisory lock key is not (registered class literal, object)'::text AS text,
            fn.name
           FROM fn,
            LATERAL regexp_matches(fn.src, 'pg_(?:try_)?advisory_xact_lock(?:_shared)?\s*\(\s*([^,)]{0,12}[,)]?)'::text, 'gi'::text) m(g)
          WHERE (m.g[1] !~ '^\d+,'::text)
        UNION ALL
         SELECT (('93 advisory lock class '::text || rtrim(m.g[1], ','::text)) || ' is not registered'::text),
            fn.name
           FROM fn,
            LATERAL regexp_matches(fn.src, 'pg_(?:try_)?advisory_xact_lock(?:_shared)?\s*\(\s*([^,)]{0,12}[,)]?)'::text, 'gi'::text) m(g)
          WHERE ((m.g[1] ~ '^\d+,'::text) AND (NOT (EXISTS ( SELECT 1
                   FROM kernel.lock_keys k
                  WHERE ((k.class_id)::text = rtrim(m.g[1], ','::text))))))
        )
 SELECT rule,
    object
   FROM cat;
CREATE OR REPLACE VIEW kernel.pin_violations WITH (security_invoker = true) AS
WITH RECURSIVE s AS (
         SELECT settings.one,
            settings.owner,
            settings.migrate,
            settings.app_group,
            settings.jobs,
            settings.role_prefix,
            settings.vetted_prefix,
            settings.backup,
            settings.restore,
            settings.ro,
            settings.services,
            settings.admins,
            settings.extensions,
            settings.org_id_pattern,
            settings.importer
           FROM kernel.settings
        ), nsp AS (
         SELECT n.oid,
            n.nspname
           FROM pg_namespace n
          WHERE ((n.nspname <> ALL (ARRAY['pg_catalog'::name, 'information_schema'::name, 'pg_toast'::name])) AND (n.nspname !~~ 'pg_temp%'::text) AND (n.nspname !~~ 'pg_toast_temp%'::text))
        ), trusted AS (
         SELECT r.oid
           FROM pg_roles r,
            s
          WHERE ((r.rolname = s.owner) OR (r.rolname = s.migrate) OR (r.rolname = s.backup) OR (r.rolname = s.restore) OR (r.rolname ~~ (s.vetted_prefix || '%'::text)))
        ), admin_roles AS (
         SELECT r.oid
           FROM pg_roles r,
            s
          WHERE (r.rolname = ANY (s.admins))
        ), rall AS (
         SELECT r.oid
           FROM pg_roles r
          WHERE ((NOT r.rolsuper) AND (r.rolname !~~ 'pg\_%'::text) AND (r.oid IS DISTINCT FROM kernel.bootstrap_admin()))
        ), actors AS (
         SELECT rall.oid
           FROM rall
          WHERE ((NOT (rall.oid IN ( SELECT trusted.oid
                   FROM trusted))) AND (NOT (rall.oid IN ( SELECT admin_roles.oid
                   FROM admin_roles))))
        ), reach AS (
         WITH RECURSIVE t(actor, role) AS (
                 SELECT a.oid,
                    a.oid
                   FROM rall a
                UNION
                 SELECT t_1.actor,
                    m.roleid
                   FROM (t t_1
                     JOIN pg_auth_members m ON ((m.member = t_1.role)))
                )
         SELECT t.actor,
            t.role
           FROM t
        ), reach_roles AS (
         SELECT DISTINCT reach.role AS oid
           FROM reach
          WHERE (reach.actor IN ( SELECT actors.oid
                   FROM actors))
        ), pk AS (
         SELECT i.indrelid AS rel,
            array_agg(a.attname ORDER BY k.ord) AS cols
           FROM ((pg_index i
             CROSS JOIN LATERAL unnest((i.indkey)::smallint[]) WITH ORDINALITY k(attnum, ord))
             JOIN pg_attribute a ON (((a.attrelid = i.indrelid) AND (a.attnum = k.attnum))))
          WHERE (i.indisprimary AND (i.indrelid IN ( SELECT vetted_write_pins.rel
                   FROM kernel.vetted_write_pins)))
          GROUP BY i.indrelid
        ), pins AS (
         SELECT p.proc AS sig,
            (p.proc)::oid AS fn,
            p.rel,
            p.op,
            p.key_cols,
            p.set_cols,
            p.txn_col,
            p.max_items,
            COALESCE(lower((((n.nspname)::text || '.'::text) || (c.relname)::text)), (p.rel)::text) AS qrel,
            n.nspname AS rel_nsp,
            c.relname,
            pk.cols AS pk_cols
           FROM (((kernel.vetted_write_pins p
             LEFT JOIN pg_class c ON ((c.oid = (p.rel)::oid)))
             LEFT JOIN pg_namespace n ON ((n.oid = c.relnamespace)))
             LEFT JOIN pk ON ((pk.rel = (p.rel)::oid)))
        ), pinrel AS (
         SELECT p.rel AS oid
           FROM pins p
        UNION
         SELECT t.relid
           FROM pins p,
            LATERAL pg_partition_tree(p.rel) t(relid, parentrelid, isleaf, level)
        ), vroles AS (
         SELECT r.oid
           FROM pg_roles r,
            s
          WHERE (r.rolname ~~ (s.vetted_prefix || '%'::text))
        ), rtn_all AS (
         SELECT p.oid,
            ((p.oid)::regprocedure)::text AS name,
            p.proname,
            p.proowner,
            (p.prosqlbody IS NOT NULL) AS atomic_body,
            kernel.src_text(p.prosrc) AS src,
            kernel.src_text(p.prosrc, true) AS srck
           FROM ((pg_proc p
             JOIN nsp n ON ((n.oid = p.pronamespace)))
             JOIN pg_language l ON (((l.oid = p.prolang) AND (l.lanname = ANY (ARRAY['sql'::name, 'plpgsql'::name])))))
          WHERE (NOT (EXISTS ( SELECT 1
                   FROM pg_depend d
                  WHERE ((d.classid = ('pg_proc'::regclass)::oid) AND (d.objid = p.oid) AND (d.deptype = 'e'::"char")))))
        ), calls AS (
         SELECT r.oid AS caller,
            lower(m.m[1]) AS callee
           FROM rtn_all r,
            LATERAL regexp_matches(r.src, '(?:^|[^a-z0-9_$])([a-z_][a-z0-9_$]*) *\('::text, 'gi'::text) m(m)
        ), pinned_rowtypes AS (
         SELECT t.oid
           FROM pg_type t
          WHERE (t.typrelid IN ( SELECT pinrel.oid
                   FROM pinrel))
        ), catalog_runs AS (
         SELECT tg.tgfoid AS oid
           FROM pg_trigger tg
          WHERE ((tg.tgrelid IN ( SELECT pinrel.oid
                   FROM pinrel)) AND (NOT tg.tgisinternal))
        UNION
         SELECT d.refobjid
           FROM (pg_attrdef ad
             JOIN pg_depend d ON (((d.classid = ('pg_attrdef'::regclass)::oid) AND (d.objid = ad.oid) AND (d.refclassid = ('pg_proc'::regclass)::oid))))
          WHERE (ad.adrelid IN ( SELECT pinrel.oid
                   FROM pinrel))
        UNION
         SELECT d.refobjid
           FROM (pg_constraint k
             JOIN pg_depend d ON (((d.classid = ('pg_constraint'::regclass)::oid) AND (d.objid = k.oid) AND (d.refclassid = ('pg_proc'::regclass)::oid))))
          WHERE (k.conrelid IN ( SELECT pinrel.oid
                   FROM pinrel))
        UNION
         SELECT d.refobjid
           FROM (pg_index i
             JOIN pg_depend d ON (((d.classid = ('pg_class'::regclass)::oid) AND (d.objid = i.indexrelid) AND (d.refclassid = ('pg_proc'::regclass)::oid))))
          WHERE (i.indrelid IN ( SELECT pinrel.oid
                   FROM pinrel))
        UNION
         SELECT p.oid
           FROM pg_proc p
          WHERE ((p.proargtypes)::oid[] && ( SELECT COALESCE(array_agg(pinned_rowtypes.oid), '{}'::oid[]) AS "coalesce"
                   FROM pinned_rowtypes))
        UNION
         SELECT (ap.amproc)::oid AS amproc
           FROM (((pg_index i
             CROSS JOIN LATERAL unnest((i.indclass)::oid[]) oc(oid))
             JOIN pg_opclass o ON ((o.oid = oc.oid)))
             JOIN pg_amproc ap ON ((ap.amprocfamily = o.opcfamily)))
          WHERE (i.indrelid IN ( SELECT pinrel.oid
                   FROM pinrel))
        UNION
         SELECT (ap.amproc)::oid AS amproc
           FROM (pg_opclass o
             JOIN pg_amproc ap ON ((ap.amprocfamily = o.opcfamily)))
          WHERE ((EXISTS ( SELECT 1
                   FROM pins)) AND (o.opcnamespace IN ( SELECT nsp.oid
                   FROM nsp)))
        UNION
         SELECT (o.oprcode)::oid AS oprcode
           FROM pg_operator o
          WHERE ((EXISTS ( SELECT 1
                   FROM pins)) AND (o.oprnamespace IN ( SELECT nsp.oid
                   FROM nsp)))
        UNION
         SELECT c.castfunc
           FROM pg_cast c
          WHERE ((EXISTS ( SELECT 1
                   FROM pins)) AND (c.castfunc <> (0)::oid))
        UNION
         SELECT d.refobjid
           FROM (pg_constraint k
             JOIN pg_depend d ON (((d.classid = ('pg_constraint'::regclass)::oid) AND (d.objid = k.oid) AND (d.refclassid = ('pg_proc'::regclass)::oid))))
          WHERE ((EXISTS ( SELECT 1
                   FROM pins)) AND (k.contypid <> (0)::oid))
        ), scoped(oid) AS (
         SELECT r.oid
           FROM rtn_all r
          WHERE ((r.proowner IN ( SELECT vroles.oid
                   FROM vroles)) OR (r.oid IN ( SELECT catalog_runs.oid
                   FROM catalog_runs)))
        UNION
         SELECT x.oid
           FROM (scoped sc
             JOIN calls k ON ((k.caller = sc.oid))),
            LATERAL ( SELECT a.oid
                   FROM rtn_all a
                  WHERE ((lower((a.proname)::text) = k.callee) AND (a.oid <> k.caller))
                UNION
                 SELECT a.oid
                   FROM ((pg_proc ag
                     JOIN pg_aggregate pa ON (((pa.aggfnoid)::oid = ag.oid)))
                     JOIN rtn_all a ON (((a.oid = (pa.aggtransfn)::oid) OR (a.oid = (pa.aggfinalfn)::oid) OR (a.oid = (pa.aggcombinefn)::oid) OR (a.oid = (pa.aggserialfn)::oid) OR (a.oid = (pa.aggdeserialfn)::oid) OR (a.oid = (pa.aggmtransfn)::oid) OR (a.oid = (pa.aggminvtransfn)::oid) OR (a.oid = (pa.aggmfinalfn)::oid))))
                  WHERE ((lower((ag.proname)::text) = k.callee) AND (ag.pronamespace IN ( SELECT nsp.oid
                           FROM nsp)))) x
        ), rtn AS (
         SELECT r.oid,
            r.name,
            r.proname,
            r.proowner,
            r.atomic_body,
            r.src,
            r.srck
           FROM (scoped sc
             JOIN rtn_all r ON ((r.oid = sc.oid)))
        ), named AS MATERIALIZED (
         SELECT p.oid
           FROM (((calls k
             JOIN scoped sc ON ((sc.oid = k.caller)))
             JOIN pg_proc p ON ((lower((p.proname)::text) = k.callee)))
             JOIN nsp n ON ((n.oid = p.pronamespace)))
        UNION
         SELECT s.fn
           FROM (((calls k
             JOIN scoped sc ON ((sc.oid = k.caller)))
             JOIN pg_proc ag ON (((lower((ag.proname)::text) = k.callee) AND (ag.prokind = 'a'::"char"))))
             JOIN pg_aggregate pa ON (((pa.aggfnoid)::oid = ag.oid))),
            LATERAL unnest(ARRAY[(pa.aggtransfn)::oid, (pa.aggfinalfn)::oid, (pa.aggcombinefn)::oid, (pa.aggserialfn)::oid, (pa.aggdeserialfn)::oid, (pa.aggmtransfn)::oid, (pa.aggminvtransfn)::oid, (pa.aggmfinalfn)::oid]) s(fn)
        ), other_lang AS (
         SELECT p.oid,
            ((p.oid)::regprocedure)::text AS name,
            p.proname,
            p.proowner
           FROM ((pg_proc p
             JOIN nsp n ON ((n.oid = p.pronamespace)))
             JOIN pg_language l ON (((l.oid = p.prolang) AND (l.lanname <> ALL (ARRAY['sql'::name, 'plpgsql'::name, 'internal'::name])))))
          WHERE (NOT (EXISTS ( SELECT 1
                   FROM pg_depend d
                  WHERE ((d.classid = ('pg_proc'::regclass)::oid) AND (d.objid = p.oid) AND (d.deptype = 'e'::"char")))))
        ), defs AS (
         SELECT p.oid,
            ((p.oid)::regprocedure)::text AS name,
            p.proname,
            n.nspname,
            p.prosrc,
            (p.prosqlbody IS NOT NULL) AS atomic_body,
            kernel.src_text(p.prosrc) AS src,
            (p.oid IN ( SELECT pins.fn
                   FROM pins)) AS is_pinned,
            (p.oid IN ( SELECT scoped.oid
                   FROM scoped)) AS in_scope
           FROM (pg_proc p
             JOIN nsp n ON ((n.oid = p.pronamespace)))
          WHERE p.prosecdef
        ), fn AS (
         SELECT p.oid,
            ((p.oid)::regprocedure)::text AS name,
            p.proowner,
            p.prosecdef,
            n.nspname,
            kernel.src_text(p.prosrc) AS src,
            p.proargnames,
            (p.proargtypes)::oid[] AS argtypes,
            g.class,
            g.scope,
            g.writer,
            COALESCE(p.proacl, acldefault('f'::"char", p.proowner)) AS acl
           FROM ((pg_proc p
             JOIN nsp n ON ((n.oid = p.pronamespace)))
             LEFT JOIN kernel.function_registry g ON (((g.proc)::oid = p.oid)))
          WHERE (p.oid IN ( SELECT pins.fn
                   FROM pins))
        ), piece AS (
         SELECT f.oid AS fn,
            f.name,
            f.nspname,
            btrim(s.t) AS st
           FROM fn f,
            LATERAL regexp_split_to_table(f.src, ';'::text) s(t)
        ), piece_n AS (
         SELECT p.fn,
            p.name,
            p.nspname,
            p.st,
            ( SELECT count(*) AS count
                   FROM regexp_matches(p.st, '\m(?:insert into|delete from)\M|\mupdate [a-z_][a-z0-9_.]*(?: as)?(?: [a-z_][a-z0-9_]*)? set\M'::text, 'gi'::text) regexp_matches(regexp_matches)) AS n
           FROM piece p
        ), ops AS (
         SELECT d.fn,
            d.name,
            d.st AS whole,
            x.stmt,
                CASE
                    WHEN (x.stmt ~* '^insert into'::text) THEN
                    CASE
                        WHEN (x.stmt ~* '\mon conflict\M'::text) THEN 'upsert'::text
                        ELSE 'insert'::text
                    END
                    WHEN (x.stmt ~* '^delete from'::text) THEN 'delete'::text
                    ELSE 'update'::text
                END AS op,
                CASE
                    WHEN (t.t ~~ '%.%'::text) THEN t.t
                    ELSE ((lower((d.nspname)::text) || '.'::text) || t.t)
                END AS tgt
           FROM piece_n d,
            LATERAL ( SELECT btrim((regexp_match(d.st, '(\m(?:insert into|delete from)\M.*|\mupdate [a-z_][a-z0-9_.]*(?: as)?(?: [a-z_][a-z0-9_]*)? set\M.*)$'::text, 'i'::text))[1]) AS stmt) x,
            LATERAL ( SELECT lower((regexp_match(x.stmt, '^(?:insert into|delete from|update) ([a-z_][a-z0-9_.]*)'::text, 'i'::text))[1]) AS t) t
          WHERE (d.n = 1)
        ), others AS (
         SELECT d.oid AS fn,
            d.name,
                CASE
                    WHEN (lower(m.g[1]) ~~ '%.%'::text) THEN lower(m.g[1])
                    ELSE ((lower((d.nspname)::text) || '.'::text) || lower(m.g[1]))
                END AS tgt
           FROM defs d,
            LATERAL regexp_matches(d.src, '\m(?:insert into|delete from|update|truncate(?: table)?|merge into)(?: only)? ([a-z_][a-z0-9_.]*)'::text, 'gi'::text) m(g)
          WHERE ((NOT d.is_pinned) AND (EXISTS ( SELECT 1
                   FROM kernel.vetted_write_pins)))
        ), dels AS (
         SELECT o.fn,
            o.name,
            p.sig,
            p.key_cols,
            p.txn_col,
            p.max_items,
            o.stmt,
            regexp_match(o.stmt, '^delete from [a-z_][a-z0-9_.]*(?: as)? ([a-z_][a-z0-9_]*) using unnest\(([a-z_][a-z0-9_]*(?:, [a-z_][a-z0-9_]*)*)\) as ([a-z_][a-z0-9_]*)\(([a-z_][a-z0-9_]*(?:, [a-z_][a-z0-9_]*)*)\) where \1\.org_id = v_org((?: and \1\.[a-z_][a-z0-9_]* = \3\.[a-z_][a-z0-9_]*)+)$'::text, 'i'::text) AS ra,
            regexp_match(o.stmt, '^delete from [a-z_][a-z0-9_.]*(?: as)? ([a-z_][a-z0-9_]*) where \1\.org_id = v_org((?: and \1\.[a-z_][a-z0-9_]* = [a-z_][a-z0-9_]*)+)$'::text, 'i'::text) AS rs,
            regexp_match(o.stmt, '^delete from [a-z_][a-z0-9_.]*(?: as)? ([a-z_][a-z0-9_]*) where \1\.org_id = v_org and \1\.([a-z_][a-z0-9_]*) = pg_current_xact_id\(\)$'::text, 'i'::text) AS rt
           FROM (ops o
             JOIN pins p ON (((p.fn = o.fn) AND (p.qrel = o.tgt) AND (p.op = 'delete'::text))))
          WHERE (o.op = 'delete'::text)
        ), dels2 AS (
         SELECT d.fn,
            d.name,
            d.sig,
            d.key_cols,
            d.txn_col,
            d.max_items,
            d.stmt,
            d.ra,
            d.rs,
            d.rt,
            ( SELECT array_agg(lower(m.m[1]) ORDER BY (lower(m.m[1]))) AS array_agg
                   FROM regexp_matches(COALESCE(d.ra[5], d.rs[2], ''::text), ' and [a-z_][a-z0-9_]*\.([a-z_][a-z0-9_]*) = '::text, 'gi'::text) m(m)) AS lefts,
            ( SELECT array_agg(lower(m.m[1]) ORDER BY (lower(m.m[1]))) AS array_agg
                   FROM regexp_matches(COALESCE(d.ra[5], ''::text), ' = [a-z_][a-z0-9_]*\.([a-z_][a-z0-9_]*)'::text, 'gi'::text) m(m)) AS rights,
            ( SELECT array_agg(lower(c.c) ORDER BY (lower(c.c))) AS array_agg
                   FROM unnest(string_to_array(d.ra[4], ', '::text)) c(c)) AS kcols,
            ( SELECT array_agg(((lower(m.m[1]) || '='::text) || lower(m.m[2])) ORDER BY ((lower(m.m[1]) || '='::text) || lower(m.m[2]))) AS array_agg
                   FROM regexp_matches(COALESCE(d.ra[5], ''::text), ' and [a-z_][a-z0-9_]*\.([a-z_][a-z0-9_]*) = [a-z_][a-z0-9_]*\.([a-z_][a-z0-9_]*)'::text, 'gi'::text) m(m)) AS pairs,
            ( SELECT array_agg(((lower((x.k)::text) || '='::text) || lower((string_to_array(d.ra[4], ', '::text))[x.ord])) ORDER BY ((lower((x.k)::text) || '='::text) || lower((string_to_array(d.ra[4], ', '::text))[x.ord]))) AS array_agg
                   FROM unnest(d.key_cols) WITH ORDINALITY x(k, ord)) AS want_pairs,
            string_to_array(d.ra[2], ', '::text) AS arr_args,
            ( SELECT array_agg(m.m[1]) AS array_agg
                   FROM regexp_matches(COALESCE(d.rs[2], ''::text), ' = ([a-z_][a-z0-9_]*)'::text, 'gi'::text) m(m)) AS scalar_args
           FROM dels d
        ), ups AS (
         SELECT o.fn,
            o.name,
            p.sig,
            p.set_cols,
            p.pk_cols,
            p.max_items,
            o.stmt,
            regexp_match(o.stmt, '^(.*?) on conflict \(([^)]*)\) do (nothing|update set (.*?))(?: returning (.*))?$'::text, 'i'::text) AS m
           FROM (ops o
             JOIN pins p ON (((p.fn = o.fn) AND (p.qrel = o.tgt) AND (p.op = 'upsert'::text))))
          WHERE (o.op = 'upsert'::text)
        ), ups2 AS (
         SELECT u.fn,
            u.name,
            u.sig,
            u.set_cols,
            u.pk_cols,
            u.max_items,
            u.stmt,
            u.m,
            regexp_match(u.m[1], '^insert into [a-z_][a-z0-9_.]* \(org_id(?:, [a-z_][a-z0-9_]*)+\) select v_org((?:, (?:[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*|-?[0-9]+|true|false|null|''''))*) from unnest\(([a-z_][a-z0-9_]*(?:, [a-z_][a-z0-9_]*)*)\) as ([a-z_][a-z0-9_]*)\(([a-z_][a-z0-9_]*(?:, [a-z_][a-z0-9_]*)*)\)$'::text, 'i'::text) AS hu,
            regexp_match(u.m[1], '^insert into [a-z_][a-z0-9_.]* \(org_id(?:, [a-z_][a-z0-9_]*)+\) values \(v_org(?:, (?:[a-z_][a-z0-9_]*|-?[0-9]+|true|false|null|''''))*\)$'::text, 'i'::text) AS hv,
            ( SELECT array_agg(lower(c.c) ORDER BY (lower(c.c))) AS array_agg
                   FROM unnest(string_to_array(u.m[2], ', '::text)) c(c)) AS conflict_sorted,
            string_to_array(lower(u.m[2]), ', '::text) AS conflict_cols,
            ( SELECT array_agg(lower(a.a[1]) ORDER BY (lower(a.a[1]))) AS array_agg
                   FROM unnest(string_to_array(u.m[4], ', '::text)) e(e),
                    LATERAL regexp_match(e.e, '^([a-z_][a-z0-9_]*) = excluded\.\1$'::text, 'i'::text) a(a)) AS set_found,
            cardinality(string_to_array(u.m[4], ', '::text)) AS set_listed,
            string_to_array(u.m[5], ', '::text) AS ret_cols
           FROM ups u
        ), arrays AS (
         SELECT d.fn,
            d.name,
            a.arg,
            d.max_items
           FROM dels2 d,
            LATERAL unnest(d.arr_args) a(arg)
          WHERE (d.ra IS NOT NULL)
        UNION ALL
         SELECT u.fn,
            u.name,
            a.arg,
            u.max_items
           FROM ups2 u,
            LATERAL unnest(string_to_array(u.hu[2], ', '::text)) a(arg)
          WHERE (u.hu IS NOT NULL)
        ), priv AS (
         SELECT p.rel,
            bool_or((p.op = ANY (ARRAY['insert'::text, 'upsert'::text]))) AS ins,
            bool_or((p.op = ANY (ARRAY['update'::text, 'upsert'::text]))) AS upd,
            bool_or((p.op = 'delete'::text)) AS del
           FROM kernel.vetted_write_pins p
          GROUP BY p.rel
        ), tree AS (
         SELECT pr.rel AS root,
            pr.rel AS oid
           FROM priv pr
        UNION
         SELECT pr.rel,
            t.relid
           FROM priv pr,
            LATERAL pg_partition_tree(pr.rel) t(relid, parentrelid, isleaf, level)
        ), grants AS (
         SELECT t.root,
            c.oid,
            a.privilege_type,
            a.grantee,
            NULL::name AS col
           FROM (tree t
             JOIN pg_class c ON ((c.oid = (t.oid)::oid))),
            LATERAL aclexplode(c.relacl) a(grantor, grantee, privilege_type, is_grantable)
        UNION ALL
         SELECT t.root,
            at.attrelid,
            a.privilege_type,
            a.grantee,
            at.attname
           FROM (tree t
             JOIN pg_attribute at ON (((at.attrelid = (t.oid)::oid) AND (at.attacl IS NOT NULL) AND (NOT at.attisdropped)))),
            LATERAL aclexplode(at.attacl) a(grantor, grantee, privilege_type, is_grantable)
        ), vdep AS (
         SELECT w.ev_class AS view,
            d.refobjid AS base
           FROM (pg_depend d
             JOIN pg_rewrite w ON ((w.oid = d.objid)))
          WHERE ((d.classid = ('pg_rewrite'::regclass)::oid) AND (d.refclassid = ('pg_class'::regclass)::oid) AND (d.refobjid IN ( SELECT pinrel.oid
                   FROM pinrel)) AND (w.ev_class <> d.refobjid))
        UNION
         SELECT w.ev_class,
            v.base
           FROM ((vdep v
             JOIN pg_depend d ON (((d.refobjid = v.view) AND (d.classid = ('pg_rewrite'::regclass)::oid) AND (d.refclassid = ('pg_class'::regclass)::oid))))
             JOIN pg_rewrite w ON ((w.oid = d.objid)))
          WHERE (w.ev_class <> v.view)
        ), orgvar AS (
         SELECT f.oid AS fn,
            f.name,
            regexp_replace(regexp_replace(regexp_replace(regexp_replace(f.src, 'v_org text := NULLIF\(current_setting\( '''', true\), ''''\)'::text, ' '::text, 'gi'::text), '\mv_org IS NULL\M'::text, ' '::text, 'gi'::text), '\morg_id = v_org\M'::text, ' '::text, 'gi'::text), '\m(select|values \() ?v_org,'::text, ' '::text, 'gi'::text) AS rest,
            ( SELECT count(*) AS count
                   FROM regexp_matches(f.src, '\mv_org text\M'::text, 'gi'::text) regexp_matches(regexp_matches)) AS declarations
           FROM fn f
        ), cat AS (
         SELECT '100 pin names a function that no longer exists'::text AS rule,
            (p.sig)::text AS object
           FROM pins p
          WHERE (NOT (EXISTS ( SELECT 1
                   FROM pg_proc x
                  WHERE (x.oid = p.fn))))
        UNION ALL
         SELECT '101 pinned function is not a registered SECURITY DEFINER org-scoped write function'::text AS text,
            f.name
           FROM fn f
          WHERE ((NOT f.prosecdef) OR (f.class IS DISTINCT FROM 'write'::text) OR (f.scope IS DISTINCT FROM 'org'::text))
        UNION ALL
         SELECT '102 pinned function is not owned by the vetted role of the pinned table''s schema'::text AS text,
            (p.sig)::text AS sig
           FROM (pins p
             JOIN fn f ON ((f.oid = p.fn))),
            s
          WHERE (f.proowner IS DISTINCT FROM (to_regrole((s.vetted_prefix || (p.rel_nsp)::text)))::oid)
        UNION ALL
         SELECT '103 pinned relation is not the root of a registered org table with vetted_write'::text AS text,
            p.qrel
           FROM pins p
          WHERE ((NOT (EXISTS ( SELECT 1
                   FROM kernel.rls_registry g
                  WHERE (((g.rel)::oid = (p.rel)::oid) AND (g.class = 'org'::text) AND g.vetted_write)))) OR (EXISTS ( SELECT 1
                   FROM pg_class c
                  WHERE ((c.oid = (p.rel)::oid) AND c.relispartition))))
        UNION ALL
         SELECT '104 pin does not match the table''s primary key or columns'::text AS text,
            (((p.sig)::text || ' -> '::text) || p.qrel)
           FROM pins p
          WHERE (((p.op = ANY (ARRAY['delete'::text, 'upsert'::text])) AND ((p.pk_cols IS NULL) OR (p.pk_cols[1] IS DISTINCT FROM 'org_id'::name))) OR ((p.key_cols IS NOT NULL) AND (NOT ((cardinality(p.key_cols) = (cardinality(p.pk_cols) - 1)) AND (p.key_cols @> p.pk_cols[2:]) AND (p.key_cols <@ p.pk_cols[2:])))) OR ((p.set_cols IS NOT NULL) AND (p.set_cols && p.pk_cols)) OR ((p.txn_col IS NOT NULL) AND (NOT (EXISTS ( SELECT 1
                   FROM pg_attribute a
                  WHERE ((a.attrelid = (p.rel)::oid) AND (a.attname = p.txn_col) AND (a.atttypid = ('xid8'::regtype)::oid) AND (NOT a.attisdropped)))))))
        UNION ALL
         SELECT (((('105 pinned function runs '::text || o.op) || ' on '::text) || COALESCE(o.tgt, '?'::text)) || ', which it does not pin'::text),
            o.name
           FROM ops o
          WHERE (NOT (EXISTS ( SELECT 1
                   FROM pins p
                  WHERE ((p.fn = o.fn) AND (p.qrel = o.tgt) AND (p.op = o.op)))))
        UNION ALL
         SELECT ((('105 pin names an operation the function never performs: '::text || p.op) || ' on '::text) || p.qrel),
            (p.sig)::text AS sig
           FROM (pins p
             JOIN fn f ON ((f.oid = p.fn)))
          WHERE (NOT (EXISTS ( SELECT 1
                   FROM ops o
                  WHERE ((o.fn = p.fn) AND (o.tgt = p.qrel) AND (o.op = p.op)))))
        UNION ALL
         SELECT '105 pinned function truncates'::text AS text,
            f.name
           FROM fn f
          WHERE (f.src ~* '\mtruncate\M'::text)
        UNION ALL
         SELECT '105 pinned function uses MERGE or ONLY, which no pinned shape contains'::text AS text,
            f.name
           FROM fn f
          WHERE (f.src ~* '\m(merge|only)\M'::text)
        UNION ALL
         SELECT '105 a statement of a pinned function has more than one DML keyword, or a WITH in front of one'::text AS text,
            p.name
           FROM piece_n p
          WHERE ((p.n > 1) OR ((p.n = 1) AND (p.st ~* '\mwith\M.*\m(insert into|delete from|update [a-z_][a-z0-9_.]*(?: as)?(?: [a-z_][a-z0-9_]*)? set)\M'::text)))
        UNION ALL
         SELECT '106 delete is not an equality on the whole primary key (or on the current transaction) of the caller''s org'::text AS text,
            d.name
           FROM dels2 d
          WHERE (NOT (((d.key_cols IS NOT NULL) AND (d.ra IS NOT NULL) AND (d.lefts = ( SELECT array_agg(lower((c.c)::text) ORDER BY (lower((c.c)::text))) AS array_agg
                   FROM unnest(d.key_cols) c(c))) AND (cardinality(d.lefts) = cardinality(d.key_cols)) AND (d.rights = d.kcols) AND (cardinality(d.rights) = cardinality(d.kcols)) AND (cardinality(d.arr_args) = cardinality(d.kcols)) AND (( SELECT count(DISTINCT lower(c.c)) AS count
                   FROM unnest(d.arr_args) c(c)) = cardinality(d.arr_args)) AND (d.pairs = d.want_pairs)) OR ((d.key_cols IS NOT NULL) AND (d.rs IS NOT NULL) AND (d.lefts = ( SELECT array_agg(lower((c.c)::text) ORDER BY (lower((c.c)::text))) AS array_agg
                   FROM unnest(d.key_cols) c(c))) AND (cardinality(d.lefts) = cardinality(d.key_cols))) OR ((d.txn_col IS NOT NULL) AND (d.rt IS NOT NULL) AND (lower(d.rt[2]) = lower((d.txn_col)::text)))))
        UNION ALL
         SELECT '107 upsert is not an INSERT of v_org on the primary key with the pinned DO UPDATE SET list'::text AS text,
            u.name
           FROM ups2 u
          WHERE (NOT ((u.m IS NOT NULL) AND ((u.hu IS NOT NULL) OR (u.hv IS NOT NULL)) AND (u.conflict_cols = ( SELECT array_agg(lower((x.c)::text) ORDER BY x.ord) AS array_agg
                   FROM unnest(u.pk_cols) WITH ORDINALITY x(c, ord))) AND ((lower(u.m[3]) = 'nothing'::text) = (cardinality(u.set_cols) = 0)) AND ((lower(u.m[3]) = 'nothing'::text) OR ((u.set_listed = cardinality(u.set_cols)) AND (u.set_found = ( SELECT array_agg(lower((c.c)::text) ORDER BY (lower((c.c)::text))) AS array_agg
                   FROM unnest(u.set_cols) c(c))))) AND ((u.m[5] IS NULL) OR (NOT (EXISTS ( SELECT 1
                   FROM unnest(string_to_array(u.m[5], ', '::text)) rc(rc)
                  WHERE (lower(rc.rc) <> ALL ((u.pk_cols)::text[])))))) AND ((u.hu IS NULL) OR (( SELECT bool_and((lower(m.m[1]) = lower(u.hu[3]))) AS bool_and
                   FROM regexp_matches(u.hu[1], '([a-z_][a-z0-9_]*)\.'::text, 'gi'::text) m(m)) IS NOT FALSE))))
        UNION ALL
         SELECT (('108 unnested array '::text || a.arg) || ' is not an array argument capped by IF cardinality() > N THEN RAISE EXCEPTION before the first DML'::text),
            a.name
           FROM (arrays a
             JOIN fn f ON ((f.oid = a.fn)))
          WHERE ((NOT (a.arg = ANY (f.proargnames))) OR (NOT (EXISTS ( SELECT 1
                   FROM (unnest(f.argtypes) WITH ORDINALITY t(typ, ord)
                     JOIN pg_type ty ON ((ty.oid = t.typ)))
                  WHERE ((t.ord = array_position(f.proargnames, a.arg)) AND (ty.typcategory = 'A'::"char"))))) OR (NOT (EXISTS ( SELECT 1
                   FROM regexp_matches(f.src, (('(\mif cardinality\('::text || a.arg) || '\) > ([0-9]{1,4}) then raise exception\M)'::text), 'gi'::text) m(m)
                  WHERE (((m.m[2])::integer <= a.max_items) AND (( SELECT count(*) AS count
                           FROM regexp_matches("left"(lower(f.src), (strpos(lower(f.src), lower(m.m[1])) - 1)), '(?<!end )\mif\M'::text, 'g'::text) regexp_matches(regexp_matches)) = ( SELECT count(*) AS count
                           FROM regexp_matches("left"(lower(f.src), (strpos(lower(f.src), lower(m.m[1])) - 1)), '\mend if\M'::text, 'g'::text) regexp_matches(regexp_matches))) AND ("left"(lower(f.src), (strpos(lower(f.src), lower(m.m[1])) - 1)) !~ '\m(?:loop|case|while|foreach|exception when|return|exit|continue)\M'::text) AND (strpos(lower(f.src), lower(m.m[1])) < COALESCE(LEAST(NULLIF(strpos(lower(f.src), 'insert into'::text), 0), NULLIF(strpos(lower(f.src), 'delete from'::text), 0), NULLIF(strpos(lower(f.src), 'update '::text), 0)), 2147483647)))))))
        UNION ALL
         SELECT (('109 scalar key '::text || sa.sa) || ' of a delete is not a scalar argument of the function'::text),
            d.name
           FROM dels2 d,
            LATERAL unnest(d.scalar_args) sa(sa),
            fn f
          WHERE ((f.oid = d.fn) AND (d.rs IS NOT NULL) AND ((NOT (sa.sa = ANY (f.proargnames))) OR (EXISTS ( SELECT 1
                   FROM (unnest(f.argtypes) WITH ORDINALITY t(typ, ord)
                     JOIN pg_type ty ON ((ty.oid = t.typ)))
                  WHERE ((t.ord = array_position(f.proargnames, sa.sa)) AND (ty.typcategory = 'A'::"char"))))))
        UNION ALL
         SELECT ((((('110 '::text ||
                CASE
                    WHEN (g.grantee = (0)::oid) THEN 'PUBLIC'::text
                    ELSE ((g.grantee)::regrole)::text
                END) || ' holds '::text) || g.privilege_type) || COALESCE((' on column '::text || (g.col)::text), ''::text)) || ' on a table that only its vetted functions may write'::text),
            ((g.oid)::regclass)::text AS oid
           FROM (grants g
             JOIN priv pr ON (((pr.rel)::oid = (g.root)::oid)))
          WHERE (((g.grantee = (0)::oid) OR (g.grantee IN ( SELECT reach_roles.oid
                   FROM reach_roles))) AND (((g.privilege_type = 'INSERT'::text) AND pr.ins) OR ((g.privilege_type = 'UPDATE'::text) AND pr.upd) OR ((g.privilege_type = 'DELETE'::text) AND pr.del)))
        UNION ALL
         SELECT '111 SECURITY DEFINER body has a CR, form feed, vertical tab, $ or non-ASCII character outside a string or a comment'::text AS text,
            d.name
           FROM defs d
          WHERE (d.in_scope AND ((d.prosrc ~ '[\r\f\v]'::text) OR (regexp_replace(d.src, '(?<![\w$])\$[0-9]+'::text, ' '::text, 'g'::text) ~ '\$|[^\x01-\x7f]'::text)))
        UNION ALL
         SELECT '112 routine with a SQL-standard body (BEGIN ATOMIC or RETURN expression)'::text AS text,
            r.name
           FROM rtn r
          WHERE r.atomic_body
        UNION ALL
         SELECT '130 routine starts a statement the text checks do not read (DO, SET, RESET, PREPARE, DDL, GRANT, COPY ...)'::text AS text,
            r.name
           FROM rtn r
          WHERE (r.src ~* '(^|;|>>|\mbegin|\mthen|\melse|\mloop|\mexception) *(do|set|reset|prepare|load|listen|notify|discard|create|alter|drop|grant|revoke|copy|import|refresh|reindex|vacuum|analyze|cluster|comment|lock|checkpoint)\M'::text)
        UNION ALL
         SELECT '131 routine calls set_config for anything but lock_timeout'::text AS text,
            r.name
           FROM rtn r
          WHERE (( SELECT count(*) AS count
                   FROM regexp_matches(r.srck, '\mset_config\M'::text, 'gi'::text) regexp_matches(regexp_matches)) <> ( SELECT count(*) AS count
                   FROM regexp_matches(r.srck, '\mset_config *\( *''lock_timeout'''::text, 'gi'::text) regexp_matches(regexp_matches)))
        UNION ALL
         SELECT ('113 pinned function is executable by '::text ||
                CASE
                    WHEN (a.grantee = (0)::oid) THEN 'PUBLIC'::text
                    ELSE ((a.grantee)::regrole)::text
                END),
            f.name
           FROM fn f,
            LATERAL aclexplode(f.acl) a(grantor, grantee, privilege_type, is_grantable)
          WHERE ((a.privilege_type = 'EXECUTE'::text) AND (a.grantee <> f.proowner) AND (a.grantee IS DISTINCT FROM (to_regrole((f.writer)::text))::oid))
        UNION ALL
         SELECT (('114 function without a pin writes '::text || o.tgt) || ', which has pins'::text),
            o.name
           FROM others o
          WHERE (o.tgt IN ( SELECT pins.qrel
                   FROM pins))
        UNION ALL
         SELECT (((('115 vetted role '::text || ((g.grantee)::regrole)::text) || ' holds '::text) || g.privilege_type) || ' on a table that belongs to another schema'::text),
            ((g.oid)::regclass)::text AS oid
           FROM (grants g
             JOIN ( SELECT DISTINCT pins.rel,
                    pins.rel_nsp
                   FROM pins) p ON (((p.rel)::oid = (g.root)::oid))),
            s
          WHERE ((g.privilege_type = ANY (ARRAY['INSERT'::text, 'UPDATE'::text, 'DELETE'::text, 'TRUNCATE'::text])) AND (g.grantee IN ( SELECT r.oid
                   FROM pg_roles r
                  WHERE (r.rolname ~~ (s.vetted_prefix || '%'::text)))) AND (g.grantee IS DISTINCT FROM (to_regrole((s.vetted_prefix || (p.rel_nsp)::text)))::oid))
        UNION ALL
         SELECT '116 pinned function sets a configuration value or reads settings more than the org once'::text AS text,
            f.name
           FROM fn f
          WHERE ((f.src ~* '\mset_config\M|\mset (?:local|session)\M|\mreset\M'::text) OR (( SELECT count(*) AS count
                   FROM regexp_matches(f.src, 'current_setting\s*\('::text, 'gi'::text) regexp_matches(regexp_matches)) <> 1))
        UNION ALL
         SELECT '116 pinned function uses the org variable for anything but org_id = v_org, VALUES (v_org, ... or SELECT v_org, ... (assignment, SELECT INTO, a second declaration)'::text AS text,
            o.name
           FROM orgvar o
          WHERE ((o.rest ~* '\mv_org\M'::text) OR (o.declarations <> 1))
        UNION ALL
         SELECT (('117 '::text || o.op) || ' of a pinned function is not the plain org-bound shape'::text),
            o.name
           FROM ops o
          WHERE (((o.op = 'insert'::text) AND (o.stmt !~* '^insert into [a-z_][a-z0-9_.]* \(org_id(?:, [a-z_][a-z0-9_]*)+\) (?:values \(v_org(?:, (?:[a-z_][a-z0-9_]*|-?[0-9]+|true|false|null|''''))*\)|select v_org(?:, (?:[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*|-?[0-9]+|true|false|null|''''))* from unnest\([a-z_][a-z0-9_]*(?:, [a-z_][a-z0-9_]*)*\) as [a-z_][a-z0-9_]*\([a-z_][a-z0-9_]*(?:, [a-z_][a-z0-9_]*)*\))$'::text)) OR ((o.op = 'update'::text) AND (o.stmt !~* '^update [a-z_][a-z0-9_.]*(?: as)? ([a-z_][a-z0-9_]*) set (?!org_id\M)[a-z_][a-z0-9_]* = (?:[a-z_][a-z0-9_]*|-?[0-9]+|true|false|null|'''')(?:, (?!org_id\M)[a-z_][a-z0-9_]* = (?:[a-z_][a-z0-9_]*|-?[0-9]+|true|false|null|''''))* where \1\.org_id = v_org(?: and \1\.[a-z_][a-z0-9_]* = (?:[a-z_][a-z0-9_]*|-?[0-9]+|true|false|null|''''))*$'::text)))
        UNION ALL
         SELECT ((('120 function '::text || f.name) || ' names the pinned function '::text) || p.name),
            f.name
           FROM defs f,
            defs p
          WHERE (p.is_pinned AND (f.oid <> p.oid) AND (NOT f.is_pinned) AND (f.src ~* (('\m'::text || regexp_replace((p.proname)::text, '(\W)'::text, '\\\1'::text, 'g'::text)) || '\M'::text)))
        UNION ALL
         SELECT '121 view over a pinned relation'::text AS text,
            ((v.view)::regclass)::text AS view
           FROM vdep v
        UNION ALL
         SELECT '122 inheritance child of a pinned relation'::text AS text,
            ((i.inhrelid)::regclass)::text AS inhrelid
           FROM (pg_inherits i
             JOIN pg_class c ON ((c.oid = i.inhrelid)))
          WHERE ((i.inhparent IN ( SELECT pinrel.oid
                   FROM pinrel)) AND (NOT c.relispartition))
        UNION ALL
         SELECT '123 foreign key that cascades from or into a pinned relation'::text AS text,
            ((((k.conrelid)::regclass)::text || ' -> '::text) || ((k.confrelid)::regclass)::text)
           FROM pg_constraint k
          WHERE ((k.contype = 'f'::"char") AND ((k.conrelid IN ( SELECT pinrel.oid
                   FROM pinrel)) OR (k.confrelid IN ( SELECT pinrel.oid
                   FROM pinrel))) AND ((k.confdeltype = ANY (ARRAY['c'::"char", 'n'::"char", 'd'::"char"])) OR (k.confupdtype = ANY (ARRAY['c'::"char", 'n'::"char", 'd'::"char"]))))
        UNION ALL
         SELECT (('132 pinned function calls '::text || ((x.oid)::regprocedure)::text) || ', which is neither a pinned function nor a vetted kernel routine'::text),
            f.name
           FROM (((fn f
             JOIN LATERAL ( SELECT DISTINCT lower(m.m[1]) AS callee
                   FROM regexp_matches(f.src, '(?:^|[^a-z0-9_$])([a-z_][a-z0-9_$]*) *\('::text, 'gi'::text) m(m)) k ON (true))
             JOIN pg_proc x ON (((lower((x.proname)::text) = k.callee) AND (x.oid <> f.oid))))
             JOIN nsp xn ON ((xn.oid = x.pronamespace))),
            s
          WHERE ((NOT (x.oid IN ( SELECT pins.fn
                   FROM pins))) AND (NOT ((xn.nspname = 'kernel'::name) AND (x.proowner = (to_regrole((s.vetted_prefix || 'kernel'::text)))::oid))) AND (NOT (EXISTS ( SELECT 1
                   FROM pg_depend d
                  WHERE ((d.classid = ('pg_proc'::regclass)::oid) AND (d.objid = x.oid) AND (d.deptype = 'e'::"char"))))))
        UNION ALL
         SELECT '133 routine a vetted role can run carries an app.* setting in its configuration'::text AS text,
            ((p.oid)::regprocedure)::text AS oid
           FROM (scoped sc
             JOIN pg_proc p ON ((p.oid = sc.oid)))
          WHERE (EXISTS ( SELECT 1
                   FROM unnest(p.proconfig) c(c)
                  WHERE (c.c ~* '^app\.'::text)))
        UNION ALL
         SELECT '135 routine a vetted role can reach is in a language the checks do not read'::text AS text,
            o.name
           FROM other_lang o
          WHERE ((o.proowner IN ( SELECT vroles.oid
                   FROM vroles)) OR (o.oid IN ( SELECT catalog_runs.oid
                   FROM catalog_runs)) OR (o.oid IN ( SELECT named.oid
                   FROM named)))
        UNION ALL
         SELECT '134 routine a vetted role can run builds SQL dynamically (EXECUTE, format(), query_to_xml and the like)'::text AS text,
            r.name
           FROM rtn r
          WHERE (r.src ~* '\mexecute\M|\mformat\s*\(|\m(query|cursor|table|schema|database)_to_xml\w*'::text)
        UNION ALL
         SELECT '125 pinned function has a writer that is not a registered pin writer'::text AS text,
            f.name
           FROM fn f
          WHERE ((f.writer IS NULL) OR (NOT (f.writer IN ( SELECT pin_writers.role
                   FROM kernel.pin_writers))))
        )
 SELECT rule,
    object
   FROM cat;
CREATE OR REPLACE VIEW kernel.org_policy_violations WITH (security_invoker = true) AS
WITH RECURSIVE s AS (
         SELECT settings.one,
            settings.owner,
            settings.migrate,
            settings.app_group,
            settings.jobs,
            settings.role_prefix,
            settings.vetted_prefix,
            settings.backup,
            settings.restore,
            settings.ro,
            settings.services,
            settings.admins,
            settings.extensions,
            settings.org_id_pattern,
            settings.importer
           FROM kernel.settings
        ), trusted AS (
         SELECT r.oid
           FROM pg_roles r,
            s
          WHERE ((r.rolname = s.owner) OR (r.rolname = s.migrate) OR (r.rolname = s.backup) OR (r.rolname = s.restore) OR (r.rolname ~~ (s.vetted_prefix || '%'::text)))
        ), admin_roles AS (
         SELECT r.oid
           FROM pg_roles r,
            s
          WHERE (r.rolname = ANY (s.admins))
        ), rall AS (
         SELECT r.oid
           FROM pg_roles r
          WHERE ((NOT r.rolsuper) AND (r.rolname !~~ 'pg\_%'::text) AND (r.oid IS DISTINCT FROM kernel.bootstrap_admin()))
        ), actors AS (
         SELECT rall.oid
           FROM rall
          WHERE ((NOT (rall.oid IN ( SELECT trusted.oid
                   FROM trusted))) AND (NOT (rall.oid IN ( SELECT admin_roles.oid
                   FROM admin_roles))))
        ), reach AS (
         WITH RECURSIVE t(actor, role) AS (
                 SELECT a.oid,
                    a.oid
                   FROM rall a
                UNION
                 SELECT t_1.actor,
                    m.roleid
                   FROM (t t_1
                     JOIN pg_auth_members m ON ((m.member = t_1.role)))
                )
         SELECT t.actor,
            t.role
           FROM t
        ), reach_roles AS (
         SELECT DISTINCT reach.role AS oid
           FROM reach
          WHERE (reach.actor IN ( SELECT actors.oid
                   FROM actors))
        ), flagged AS (
         SELECT r.rel AS root
           FROM kernel.rls_registry r
          WHERE r.vetted_org_policy
        ), tree AS (
         SELECT f.root,
            f.root AS oid
           FROM flagged f
        UNION
         SELECT f.root,
            t.relid
           FROM flagged f,
            LATERAL pg_partition_tree(f.root) t(relid, parentrelid, isleaf, level)
        ), pol AS (
         SELECT p.polrelid AS oid,
            p.polname AS name,
            p.polcmd AS cmd,
            p.polpermissive AS permissive,
            pg_get_expr(p.polqual, p.polrelid) AS qual,
            pg_get_expr(p.polwithcheck, p.polrelid) AS with_check,
            ( SELECT array_agg(r.rolname ORDER BY r.rolname) AS array_agg
                   FROM pg_roles r
                  WHERE (r.oid = ANY (p.polroles))) AS roles,
            (p.polroles = '{0}'::oid[]) AS public_role
           FROM pg_policy p
        ), tmpl AS (
         SELECT policy_template.qual,
            policy_template.with_check
           FROM kernel.policy_template
          WHERE (policy_template.name = 'org_scope'::text)
        ), cat AS (
         SELECT '118 pinned table has no per-org policy for the vetted writer role'::text AS rule,
            (p.rel)::text AS object
           FROM ( SELECT DISTINCT vetted_write_pins.rel
                   FROM kernel.vetted_write_pins) p
          WHERE (NOT (EXISTS ( SELECT 1
                   FROM flagged f
                  WHERE ((f.root)::oid = (p.rel)::oid))))
        UNION ALL
         SELECT '128 table of a schema that owns pinned relations does not require the per-org policy'::text AS text,
            (r.rel)::text AS rel
           FROM (kernel.rls_registry r
             JOIN pg_class c ON ((c.oid = (r.rel)::oid)))
          WHERE ((r.class = 'org'::text) AND r.vetted_write AND (NOT r.vetted_read) AND (NOT r.vetted_org_policy) AND (EXISTS ( SELECT 1
                   FROM (kernel.vetted_write_pins p
                     JOIN pg_class pc ON ((pc.oid = (p.rel)::oid)))
                  WHERE (pc.relnamespace = c.relnamespace))))
        UNION ALL
         SELECT '119 vetted writer policy is not the per-org predicate on a table that requires it'::text AS text,
            (t.oid)::text AS oid
           FROM tree t,
            s
          WHERE (NOT (EXISTS ( SELECT 1
                   FROM pol,
                    tmpl
                  WHERE ((pol.oid = (t.oid)::oid) AND (pol.name = 'vetted_write'::name) AND (pol.cmd = '*'::"char") AND pol.permissive AND (pol.qual = tmpl.qual) AND (pol.with_check = tmpl.with_check) AND (pol.roles = ARRAY[((s.vetted_prefix || (( SELECT n.nspname
                           FROM (pg_class c
                             JOIN pg_namespace n ON ((n.oid = c.relnamespace)))
                          WHERE (c.oid = (t.root)::oid)))::text))::name])))))
        UNION ALL
         SELECT (((((('124 vetted role '::text || ((g.grantee)::regrole)::text) || ' holds '::text) || g.privilege_type) || ' on '::text) || ((g.oid)::regclass)::text) || ', which no pin needs'::text),
            ((g.oid)::regclass)::text AS oid
           FROM ( SELECT f.root,
                    c.oid,
                    a.grantee,
                    a.privilege_type
                   FROM (flagged f
                     JOIN pg_class c ON ((c.oid = (f.root)::oid))),
                    LATERAL aclexplode(c.relacl) a(grantor, grantee, privilege_type, is_grantable)) g,
            s
          WHERE ((g.grantee = (to_regrole((s.vetted_prefix || (( SELECT n.nspname
                   FROM (pg_class c
                     JOIN pg_namespace n ON ((n.oid = c.relnamespace)))
                  WHERE (c.oid = (g.root)::oid)))::text)))::oid) AND (g.privilege_type = ANY (ARRAY['INSERT'::text, 'UPDATE'::text, 'DELETE'::text, 'TRUNCATE'::text])) AND (NOT (EXISTS ( SELECT 1
                   FROM kernel.vetted_write_pins p
                  WHERE (((p.rel)::oid = (g.root)::oid) AND (((g.privilege_type = 'INSERT'::text) AND (p.op = ANY (ARRAY['insert'::text, 'upsert'::text]))) OR ((g.privilege_type = 'UPDATE'::text) AND (p.op = ANY (ARRAY['update'::text, 'upsert'::text]))) OR ((g.privilege_type = 'DELETE'::text) AND (p.op = 'delete'::text))))))))
        UNION ALL
         SELECT (((('126 '::text ||
                CASE
                    WHEN (a.grantee = (0)::oid) THEN 'PUBLIC'::text
                    ELSE ((a.grantee)::regrole)::text
                END) || ' holds '::text) || a.privilege_type) || ' on a table that only its vetted functions may write'::text),
            (t.oid)::text AS oid
           FROM (tree t
             JOIN pg_class c ON ((c.oid = (t.oid)::oid))),
            LATERAL aclexplode(c.relacl) a(grantor, grantee, privilege_type, is_grantable)
          WHERE ((a.privilege_type = ANY (ARRAY['INSERT'::text, 'UPDATE'::text, 'DELETE'::text, 'TRUNCATE'::text])) AND ((a.grantee = (0)::oid) OR (a.grantee IN ( SELECT reach_roles.oid
                   FROM reach_roles))) AND (NOT (EXISTS ( SELECT 1
                   FROM kernel.vetted_open_ops o
                  WHERE (((o.rel)::oid = (t.root)::oid) AND (upper(o.op) = a.privilege_type))))))
        UNION ALL
         SELECT (((((('127 '::text ||
                CASE
                    WHEN (a.grantee = (0)::oid) THEN 'PUBLIC'::text
                    ELSE ((a.grantee)::regrole)::text
                END) || ' holds '::text) || a.privilege_type) || ' on column '::text) || (at.attname)::text) || ' of a table that only its vetted functions may write'::text),
            (t.oid)::text AS oid
           FROM (tree t
             JOIN pg_attribute at ON (((at.attrelid = (t.oid)::oid) AND (at.attacl IS NOT NULL) AND (NOT at.attisdropped)))),
            LATERAL aclexplode(at.attacl) a(grantor, grantee, privilege_type, is_grantable)
          WHERE ((a.privilege_type = ANY (ARRAY['INSERT'::text, 'UPDATE'::text])) AND ((a.grantee = (0)::oid) OR (a.grantee IN ( SELECT reach_roles.oid
                   FROM reach_roles))) AND (NOT (EXISTS ( SELECT 1
                   FROM kernel.vetted_open_ops o
                  WHERE (((o.rel)::oid = (t.root)::oid) AND (upper(o.op) = a.privilege_type))))))
        UNION ALL
         SELECT ('119 vetted role has another policy on a table that requires the per-org predicate: '::text || (pol.name)::text),
            (t.oid)::text AS oid
           FROM (tree t
             JOIN pol ON ((pol.oid = (t.oid)::oid))),
            s
          WHERE ((pol.name <> 'vetted_write'::name) AND (pol.public_role OR (EXISTS ( SELECT 1
                   FROM unnest(pol.roles) r(r)
                  WHERE (r.r ~~ (s.vetted_prefix || '%'::text))))))
        )
 SELECT rule,
    object
   FROM cat;

-- kernel_0021's wrapper, and check 37 for the bootstrap administrator acting with one of our roles' privileges (INHERIT or SET)
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
  WHERE NOT kernel._org_erase_policy_ok(x.rel)
UNION ALL -- 37 (the bootstrap administrator holds one of our roles with INHERIT or SET, not only ADMIN)
SELECT '37 bootstrap administrator holds an application role ' || m.roleid::regrole::text, m.member::regrole::text
  FROM pg_auth_members m, kernel.settings s
  WHERE m.member = kernel.bootstrap_admin() AND (m.inherit_option OR m.set_option) AND m.roleid::regrole::text LIKE s.role_prefix || '%';
REVOKE ALL ON kernel.rls_violations FROM PUBLIC;

CREATE OR REPLACE VIEW kernel.rls_warnings WITH (security_invoker = true) AS
SELECT '43 managed platform: large object function executable by PUBLIC' AS rule, p.oid::regprocedure::text AS object
FROM pg_proc p WHERE p.pronamespace = 'pg_catalog'::regnamespace AND p.proname ~ '^(lo_|lo(read|write)$)'
  AND EXISTS (SELECT 1 FROM aclexplode(COALESCE(p.proacl, acldefault('f', p.proowner))) x WHERE x.grantee = 0 AND x.privilege_type = 'EXECUTE')
  AND (COALESCE((SELECT (profile->>'managed')::boolean FROM platform.deployment_profile), false) OR kernel.bootstrap_admin() IS NOT NULL);
