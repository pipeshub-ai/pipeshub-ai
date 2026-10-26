"""The security surface as text, and a diff against the expected text (deny by default).

``python -m db.surface`` prints the live surface of the database in ``PG_ADMIN_URL``: roles and their attributes, membership
edges with their options, every ACL (database, schema, relation, column, function, type, language, foreign server, large
object), default privileges, policies, row-security flags, rules, triggers, constraints, view and function definitions
(as hashes), SECURITY DEFINER / LEAKPROOF flags, function settings, extensions, foreign data, publications, subscriptions
and event triggers. One sorted line per fact.

``db.migrate`` compares it with the expected text: ``surface*.txt`` in ``backend/db``, an ``allowlist.yaml`` in each directory of
``EXTRA_MIGRATION_LOCATIONS`` (a ``surface:`` key and one ``- <line>`` item per line; the lines are merged into the expected state;
a ``remove:`` key drops base lines the location replaces),
plus the files named in ``PG_MIGRATE_SURFACE_FILES`` (os.pathsep separated). A line that is live but not expected,
or expected but not live, is a violation. ``PG_MIGRATE_SURFACE_CHECK=skip`` turns the comparison off (development only).
Regenerate a file with ``python -m db.surface`` on a freshly migrated database and review the diff.
"""
import os
import re
import sys
from collections.abc import Iterable
from pathlib import Path

import psycopg

from db.locations import DB_DIR, extra_locations

BOOTSTRAP_SUPERUSER = "<bootstrap-superuser>"
_G = "CASE WHEN {g} = 0 THEN 'PUBLIC' ELSE {g}::regrole::text END"
# Roles that are reviewed administrators (kernel.settings.admins) and predefined roles are left to the semantic checks.
_ROLES_OK = """(SELECT oid FROM pg_roles WHERE rolname NOT LIKE 'pg\\_%')"""
_ADMINS = """COALESCE((SELECT admins FROM kernel.settings), '{}')"""
_EXT = "COALESCE((SELECT e.extname FROM pg_depend d JOIN pg_extension e ON e.oid = d.refobjid WHERE d.classid = {cls}::regclass AND d.objid = {oid} AND d.deptype = 'e'), '-')"
_SYS = "n.nspname IN ('pg_catalog', 'information_schema')"
_NSP = "n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast') AND n.nspname NOT LIKE 'pg\\_temp%' AND n.nspname NOT LIKE 'pg\\_toast\\_temp%'"

# Partitions and indexes of a registered org-scoped table are derived, not new surface: a leaf made by maintenance (or by the
# migration) is folded into its root when its owner, row-level security flags, ACL and policies are exactly what kernel.apply_rls
# gives a partition (owner and policies of the root, privileges for backup SELECT and restore INSERT only, nothing on columns, no
# rule, and no trigger of its own: the copy of its root's trigger is derived too). Their lines (relation, columns, constraints, policies, indexes) are then left out of the comparison. A partition
# that differs in any of this, or whose root is not registered, is listed in full and so fails as unexpected.
_POLICIES = """(SELECT COALESCE(string_agg(p.polname || ' ' || p.polcmd::text || ' ' || p.polpermissive || ' ' || COALESCE(
        (SELECT string_agg(CASE WHEN x = 0 THEN 'PUBLIC' ELSE x::regrole::text END, ',' ORDER BY CASE WHEN x = 0 THEN 'PUBLIC' ELSE x::regrole::text END)
         FROM unnest(p.polroles) x), '-') || ' ' || COALESCE(pg_get_expr(p.polqual, p.polrelid), '-') || ' ' || COALESCE(pg_get_expr(p.polwithcheck, p.polrelid), '-'),
        '|' ORDER BY p.polname), '') FROM pg_policy p WHERE p.polrelid = {rel})"""
_FOLD_SELECT = f"""SELECT c.oid FROM pg_class c JOIN kernel.rls_registry g ON g.rel = pg_partition_root(c.oid) AND g.class IN ('org', 'lineage')
    JOIN pg_class r ON r.oid = g.rel CROSS JOIN kernel.settings s
    WHERE c.relispartition AND c.relowner = r.relowner AND c.relrowsecurity AND c.relforcerowsecurity
      AND NOT EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = c.oid AND a.attacl IS NOT NULL)
      AND NOT EXISTS (SELECT 1 FROM pg_trigger t WHERE t.tgrelid = c.oid AND NOT t.tgisinternal AND t.tgparentid = 0)
      AND NOT EXISTS (SELECT 1 FROM pg_rewrite w WHERE w.ev_class = c.oid)
      AND COALESCE((SELECT string_agg(g, ',' ORDER BY g) FROM (SELECT CASE WHEN a.grantee = 0 THEN 'PUBLIC' ELSE a.grantee::regrole::text END
                                                                 || ':' || a.privilege_type || ':' || a.is_grantable AS g
                    FROM aclexplode(COALESCE(c.relacl, acldefault('r', c.relowner))) a WHERE a.grantee <> c.relowner) x), '')
          = (SELECT string_agg(e, ',' ORDER BY e) FROM unnest(ARRAY[s.backup || ':SELECT:false', s.restore || ':INSERT:false']) e)
      AND {_POLICIES.format(rel="c.oid")} = {_POLICIES.format(rel="r.oid")}"""
_NOT_FOLDED = "NOT EXISTS (SELECT 1 FROM (" + _FOLD_SELECT + ") f WHERE f.oid = {rel})"

QUERIES = [
    ("role", f"""SELECT r.rolname || ' login=' || r.rolcanlogin || ' super=' || r.rolsuper || ' inherit=' || r.rolinherit || ' createrole=' || r.rolcreaterole
        || ' createdb=' || r.rolcreatedb || ' replication=' || r.rolreplication || ' bypassrls=' || r.rolbypassrls || ' connlimit=' || r.rolconnlimit
        || ' valid_until=' || CASE WHEN r.rolvaliduntil IS NULL OR r.rolvaliduntil = 'infinity' THEN '-' ELSE r.rolvaliduntil::text END
        FROM pg_roles r WHERE r.oid IN {_ROLES_OK}"""),
    ("rolesetting", """SELECT COALESCE(r.rolname, '-') || ' ' || COALESCE(d.datname, '-') || ' ' || c FROM pg_db_role_setting s
        LEFT JOIN pg_roles r ON r.oid = s.setrole LEFT JOIN pg_database d ON d.oid = s.setdatabase, unnest(s.setconfig) c
        WHERE r.oid IS NULL OR r.oid IN """ + _ROLES_OK),
    ("member", f"""SELECT m.member::regrole || ' -> ' || m.roleid::regrole || ' inherit=' || m.inherit_option || ' set=' || m.set_option || ' admin=' || m.admin_option
        FROM pg_auth_members m WHERE m.member::regrole::text NOT LIKE 'pg\\_%'
        AND NOT (m.member::regrole::text = ANY ({_ADMINS}) AND m.roleid::regrole::text = ANY ({_ADMINS}))"""),
    ("settings", """SELECT 'admins=' || array_to_string(admins, ',') || ' extensions=' || array_to_string(extensions, ',') || ' services=' || array_to_string(services, ',')
        FROM kernel.settings"""),
    ("database", "SELECT 'owner=' || d.datdba::regrole FROM pg_database d WHERE d.datname = current_database()"),
    ("databaseacl", "SELECT " + _G.format(g="a.grantee") + " || ' ' || a.privilege_type || ' grantable=' || a.is_grantable FROM pg_database d, "
        "LATERAL aclexplode(COALESCE(d.datacl, acldefault('d', d.datdba))) a WHERE d.datname = current_database() AND a.grantee <> d.datdba"),
    ("schema", f"SELECT n.nspname || ' owner=' || n.nspowner::regrole FROM pg_namespace n WHERE {_NSP}"),
    ("schemaacl", "SELECT n.nspname || ' ' || " + _G.format(g="a.grantee") + f" || ' ' || a.privilege_type || ' grantable=' || a.is_grantable FROM pg_namespace n, "
        f"LATERAL aclexplode(COALESCE(n.nspacl, acldefault('n', n.nspowner))) a WHERE {_NSP} AND a.grantee <> n.nspowner"),
    ("rel", f"""SELECT c.oid::regclass || ' kind=' || c.relkind::text || ' owner=' || c.relowner::regrole || ' rls=' || c.relrowsecurity || ' force=' || c.relforcerowsecurity
        || ' options=' || COALESCE(array_to_string(c.reloptions, ','), '-') || ' ext=' || {_EXT.format(cls="'pg_class'", oid="c.oid")}
        || CASE WHEN c.relkind IN ('v', 'm') THEN ' def=' || md5(pg_get_viewdef(c.oid)) ELSE '' END
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.relkind IN ('r', 'p', 'v', 'm', 'S', 'f') AND {_NSP} AND {_NOT_FOLDED.format(rel="c.oid")}"""),
    ("relacl", "SELECT c.oid::regclass || ' ' || " + _G.format(g="a.grantee") + f" || ' ' || a.privilege_type || ' grantable=' || a.is_grantable FROM pg_class c "
        f"JOIN pg_namespace n ON n.oid = c.relnamespace, LATERAL aclexplode(COALESCE(c.relacl, acldefault((CASE c.relkind WHEN 'S' THEN 's' ELSE 'r' END)::\"char\", c.relowner))) a "
        f"WHERE c.relkind IN ('r', 'p', 'v', 'm', 'S', 'f') AND {_NSP} AND a.grantee <> c.relowner AND {_NOT_FOLDED.format(rel="c.oid")}"),
    ("colacl", "SELECT c.oid::regclass || '.' || t.attname || ' ' || " + _G.format(g="a.grantee") + f" || ' ' || a.privilege_type FROM pg_attribute t "
        f"JOIN pg_class c ON c.oid = t.attrelid JOIN pg_namespace n ON n.oid = c.relnamespace, LATERAL aclexplode(t.attacl) a WHERE t.attacl IS NOT NULL AND NOT t.attisdropped AND {_NSP} AND {_NOT_FOLDED.format(rel="c.oid")}"),
    ("policy", f"""SELECT p.polrelid::regclass || ' ' || p.polname || ' cmd=' || p.polcmd::text || ' permissive=' || p.polpermissive
        || ' roles=' || COALESCE((SELECT string_agg(CASE WHEN x = 0 THEN 'PUBLIC' ELSE x::regrole::text END, ',' ORDER BY CASE WHEN x = 0 THEN 'PUBLIC' ELSE x::regrole::text END) FROM unnest(p.polroles) x), '-')
        || ' using=' || COALESCE(pg_get_expr(p.polqual, p.polrelid), '-') || ' check=' || COALESCE(pg_get_expr(p.polwithcheck, p.polrelid), '-')
        FROM pg_policy p JOIN pg_class c ON c.oid = p.polrelid JOIN pg_namespace n ON n.oid = c.relnamespace WHERE {_NSP} AND {_NOT_FOLDED.format(rel="c.oid")}"""),
    ("rule", f"SELECT c.oid::regclass || ' ' || w.rulename || ' def=' || md5(pg_get_ruledef(w.oid)) FROM pg_rewrite w JOIN pg_class c ON c.oid = w.ev_class "
        f"JOIN pg_namespace n ON n.oid = c.relnamespace WHERE w.rulename <> '_RETURN' AND {_NSP} AND {_NOT_FOLDED.format(rel="c.oid")}"),
    ("trigger", f"SELECT t.tgrelid::regclass || ' ' || t.tgname || ' enabled=' || t.tgenabled::text || ' function=' || t.tgfoid::regprocedure FROM pg_trigger t "
        f"JOIN pg_class c ON c.oid = t.tgrelid JOIN pg_namespace n ON n.oid = c.relnamespace WHERE NOT t.tgisinternal AND {_NSP} AND {_NOT_FOLDED.format(rel="c.oid")}"),
    ("constraint", f"SELECT k.conrelid::regclass || ' ' || k.conname || ' ' || k.contype::text || ' def=' || md5(pg_get_constraintdef(k.oid)) FROM pg_constraint k "
        f"JOIN pg_class c ON c.oid = k.conrelid JOIN pg_namespace n ON n.oid = c.relnamespace WHERE k.conparentid = 0 AND k.contype IN ('p', 'u', 'f', 'c', 'x') AND {_NSP} AND {_NOT_FOLDED.format(rel="c.oid")}"),
    ("column", f"SELECT c.oid::regclass || '.' || a.attname || ' type=' || format_type(a.atttypid, a.atttypmod) || ' notnull=' || a.attnotnull "
        f"|| ' identity=' || a.attidentity::text || ' generated=' || a.attgenerated::text || ' default=' || COALESCE(pg_get_expr(d.adbin, d.adrelid), '-') "
        f"FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid JOIN pg_namespace n ON n.oid = c.relnamespace LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum "
        f"WHERE a.attnum > 0 AND NOT a.attisdropped AND c.relkind IN ('r', 'p', 'v', 'm', 'f') AND {_NSP} AND {_NOT_FOLDED.format(rel="c.oid")}"),
    ("index", f"SELECT i.indexrelid::regclass || ' on ' || i.indrelid::regclass || ' unique=' || i.indisunique || ' exclusion=' || i.indisexclusion || ' valid=' || i.indisvalid "
        f"|| ' def=' || md5(pg_get_indexdef(i.indexrelid)) FROM pg_index i JOIN pg_class c ON c.oid = i.indrelid JOIN pg_namespace n ON n.oid = c.relnamespace WHERE {_NSP} AND {_NOT_FOLDED.format(rel="i.indrelid")}"),
    ("operator", f"SELECT o.oid::regoperator || ' owner=' || o.oprowner::regrole || ' function=' || o.oprcode FROM pg_operator o JOIN pg_namespace n ON n.oid = o.oprnamespace WHERE {_NSP}"),
    ("cast", "SELECT c.castsource::regtype || ' -> ' || c.casttarget::regtype || ' function=' || c.castfunc::regproc || ' context=' || c.castcontext::text "
        "FROM pg_cast c WHERE c.oid >= 16384"),
    # extension functions are listed only when they are SECURITY DEFINER, LEAKPROOF or carry a non-default ACL
    ("func", f"""SELECT p.oid::regprocedure || ' owner=' || p.proowner::regrole || ' secdef=' || p.prosecdef || ' leakproof=' || p.proleakproof
        || ' volatility=' || p.provolatile::text || ' language=' || l.lanname || ' config=' || COALESCE(array_to_string(p.proconfig, ';'), '-')
        || ' body=' || md5(COALESCE(p.prosrc, '')) || ' ext=' || {_EXT.format(cls="'pg_proc'", oid="p.oid")}
        FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace JOIN pg_language l ON l.oid = p.prolang
        WHERE {_NSP} AND (p.prosecdef OR p.proleakproof OR p.proacl IS NOT NULL OR NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.classid = 'pg_proc'::regclass AND d.objid = p.oid AND d.deptype = 'e'))"""),
    ("funcacl", f"""SELECT p.oid::regprocedure || ' ' || {_G.format(g="a.grantee")} || ' ' || a.privilege_type FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace,
        LATERAL aclexplode(COALESCE(p.proacl, acldefault('f', p.proowner))) a
        WHERE {_NSP} AND a.grantee <> p.proowner AND (p.prosecdef OR p.proleakproof OR p.proacl IS NOT NULL OR NOT EXISTS
          (SELECT 1 FROM pg_depend d WHERE d.classid = 'pg_proc'::regclass AND d.objid = p.oid AND d.deptype = 'e'))"""),
    ("type", f"SELECT t.oid::regtype || ' kind=' || t.typtype::text || ' owner=' || t.typowner::regrole FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace "
        f"WHERE t.typtype IN ('d', 'e', 'r', 'm') AND t.typarray <> 0 AND {_NSP}"),
    ("typeacl", "SELECT t.oid::regtype || ' ' || " + _G.format(g="a.grantee") + f" || ' ' || a.privilege_type FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace, "
        f"LATERAL aclexplode(t.typacl) a WHERE t.typacl IS NOT NULL AND t.typarray <> 0 AND {_NSP} AND a.grantee <> t.typowner"),
    ("language", "SELECT l.lanname || ' trusted=' || l.lanpltrusted || ' owner=' || l.lanowner::regrole FROM pg_language l"),
    ("languageacl", "SELECT l.lanname || ' ' || " + _G.format(g="a.grantee") + " || ' ' || a.privilege_type FROM pg_language l, "
        "LATERAL aclexplode(COALESCE(l.lanacl, acldefault('l', l.lanowner))) a WHERE a.grantee <> l.lanowner"),
    ("largeobject", "SELECT m.oid || ' owner=' || m.lomowner::regrole FROM pg_largeobject_metadata m"),
    ("largeobjectacl", "SELECT m.oid || ' ' || " + _G.format(g="a.grantee") + " || ' ' || a.privilege_type FROM pg_largeobject_metadata m, "
        "LATERAL aclexplode(m.lomacl) a WHERE m.lomacl IS NOT NULL AND a.grantee <> m.lomowner"),
    ("lofunctions", """SELECT 'public_execute=' || count(*) FROM pg_proc p WHERE p.pronamespace = 'pg_catalog'::regnamespace AND p.proname ~ '^(lo_|lo(read|write)$)'
        AND EXISTS (SELECT 1 FROM aclexplode(COALESCE(p.proacl, acldefault('f', p.proowner))) x WHERE x.grantee = 0 AND x.privilege_type = 'EXECUTE')"""),
    ("fdw", "SELECT w.fdwname || ' owner=' || w.fdwowner::regrole FROM pg_foreign_data_wrapper w"),
    ("fdwacl", "SELECT w.fdwname || ' ' || " + _G.format(g="a.grantee") + " || ' ' || a.privilege_type FROM pg_foreign_data_wrapper w, "
        "LATERAL aclexplode(COALESCE(w.fdwacl, acldefault('F', w.fdwowner))) a WHERE a.grantee <> w.fdwowner"),
    ("server", "SELECT s.srvname || ' fdw=' || (SELECT fdwname FROM pg_foreign_data_wrapper WHERE oid = s.srvfdw) || ' owner=' || s.srvowner::regrole FROM pg_foreign_server s"),
    ("serveracl", "SELECT s.srvname || ' ' || " + _G.format(g="a.grantee") + " || ' ' || a.privilege_type FROM pg_foreign_server s, "
        "LATERAL aclexplode(COALESCE(s.srvacl, acldefault('S', s.srvowner))) a WHERE a.grantee <> s.srvowner"),
    ("usermapping", "SELECT um.srvname || ' ' || COALESCE(um.usename, 'PUBLIC') FROM pg_user_mappings um"),
    ("defaultacl", "SELECT COALESCE(d.defaclrole::regrole::text, '-') || ' ' || COALESCE(NULLIF(d.defaclnamespace, 0)::regnamespace::text, '-') || ' ' || d.defaclobjtype::text || ' ' || "
        + _G.format(g="a.grantee") + " || ' ' || a.privilege_type FROM pg_default_acl d, LATERAL aclexplode(d.defaclacl) a"),
    ("publication", "SELECT p.pubname || ' all=' || p.puballtables || ' tables=' || COALESCE((SELECT string_agg(r.prrelid::regclass::text, ',' ORDER BY r.prrelid::regclass::text) "
        "FROM pg_publication_rel r WHERE r.prpubid = p.oid), '-') FROM pg_publication p"),
    ("subscription", "SELECT s.subname || ' owner=' || s.subowner::regrole FROM pg_subscription s"),
    ("eventtrigger", "SELECT e.evtname || ' event=' || e.evtevent::text || ' function=' || e.evtfoid::regprocedure || ' owner=' || e.evtowner::regrole || ' enabled=' || e.evtenabled::text FROM pg_event_trigger e"),
    # ---- the system schemas: every ACL that differs from what initdb recorded, and everything a user created there
    ("sysrelacl", f"""SELECT c.oid::regclass || ' ' || {_G.format(g="a.grantee")} || ' ' || a.privilege_type FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace,
        LATERAL aclexplode(COALESCE(c.relacl, acldefault((CASE c.relkind WHEN 'S' THEN 's' ELSE 'r' END)::\"char\", c.relowner))) a
        WHERE {_SYS} AND c.relkind IN ('r', 'p', 'v', 'm', 'S', 'f') AND a.grantee <> c.relowner
          AND c.relacl IS DISTINCT FROM (SELECT i.initprivs FROM pg_init_privs i WHERE i.objoid = c.oid AND i.classoid = 'pg_class'::regclass AND i.objsubid = 0)"""),
    ("syscolacl", f"""SELECT c.oid::regclass || '.' || t.attname || ' ' || {_G.format(g="a.grantee")} || ' ' || a.privilege_type FROM pg_attribute t JOIN pg_class c ON c.oid = t.attrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace, LATERAL aclexplode(t.attacl) a
        WHERE {_SYS} AND t.attacl IS NOT NULL AND NOT t.attisdropped
          AND t.attacl IS DISTINCT FROM (SELECT i.initprivs FROM pg_init_privs i WHERE i.objoid = c.oid AND i.classoid = 'pg_class'::regclass AND i.objsubid = t.attnum)"""),
    ("sysfuncacl", f"""SELECT p.oid::regprocedure || ' ' || {_G.format(g="a.grantee")} || ' ' || a.privilege_type FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace,
        LATERAL aclexplode(COALESCE(p.proacl, acldefault('f', p.proowner))) a
        WHERE {_SYS} AND a.grantee <> p.proowner AND p.proname !~ '^(lo_|lo(read|write)$)'
          AND p.proacl IS DISTINCT FROM (SELECT i.initprivs FROM pg_init_privs i WHERE i.objoid = p.oid AND i.classoid = 'pg_proc'::regclass AND i.objsubid = 0)"""),
    ("sysschemaacl", f"""SELECT n.nspname || ' ' || {_G.format(g="a.grantee")} || ' ' || a.privilege_type FROM pg_namespace n,
        LATERAL aclexplode(COALESCE(n.nspacl, acldefault('n', n.nspowner))) a
        WHERE {_SYS} AND a.grantee <> n.nspowner
          AND n.nspacl IS DISTINCT FROM (SELECT i.initprivs FROM pg_init_privs i WHERE i.objoid = n.oid AND i.classoid = 'pg_namespace'::regclass AND i.objsubid = 0)"""),
    ("systypeacl", f"""SELECT t.oid::regtype || ' ' || {_G.format(g="a.grantee")} || ' ' || a.privilege_type FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace,
        LATERAL aclexplode(t.typacl) a WHERE {_SYS} AND t.typacl IS NOT NULL AND a.grantee <> t.typowner
          AND t.typacl IS DISTINCT FROM (SELECT i.initprivs FROM pg_init_privs i WHERE i.objoid = t.oid AND i.classoid = 'pg_type'::regclass AND i.objsubid = 0)"""),
    ("sysobject", f"""SELECT 'relation ' || c.oid::regclass || ' owner=' || c.relowner::regrole || ' ext=' || {_EXT.format(cls="'pg_class'", oid="c.oid")}
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE {_SYS} AND c.oid >= 16384
        UNION ALL SELECT 'function ' || p.oid::regprocedure || ' owner=' || p.proowner::regrole || ' secdef=' || p.prosecdef || ' body=' || md5(COALESCE(p.prosrc, ''))
          || ' ext=' || {_EXT.format(cls="'pg_proc'", oid="p.oid")} FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace WHERE {_SYS} AND p.oid >= 16384
        UNION ALL SELECT 'type ' || t.oid::regtype FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace WHERE {_SYS} AND t.oid >= 16384
        UNION ALL SELECT 'operator ' || o.oid::regoperator FROM pg_operator o JOIN pg_namespace n ON n.oid = o.oprnamespace WHERE {_SYS} AND o.oid >= 16384"""),
    # Built-in functions: one hash per PostgreSQL major over what decides who can do what and is the same in every build of that major:
    # identity (name and argument types; the OID is not used because initdb assigns it, and it shifts with the locales the build imports),
    # language, owner, SECURITY DEFINER, LEAKPROOF, volatility, kind and settings, sorted with the C collation so the order does not depend
    # on the database locale. The body (prosrc) and probin are left out on purpose: they can differ between builds (distribution packages,
    # library paths) and only a superuser, who is outside the threat model, can change them.
    # A readable line follows for any built-in that runs with its owner's rights or has settings.
    ("sysfuncs", f"""SELECT 'count=' || count(*) || ' md5=' || md5(string_agg(p.oid::regprocedure || ':' || l.lanname || ':' || (p.proowner = 10) || ':' || p.prosecdef
        || ':' || p.proleakproof || ':' || p.provolatile::text || ':' || p.prokind::text || ':' || COALESCE(array_to_string(p.proconfig, ','), ''), E'\\n'
        ORDER BY (p.oid::regprocedure::text) COLLATE "C"))
        FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace JOIN pg_language l ON l.oid = p.prolang WHERE {_SYS} AND p.oid < 16384"""),
    ("sysfunc", f"""SELECT p.oid::regprocedure || ' language=' || l.lanname || ' secdef=' || p.prosecdef || ' config=' || COALESCE(array_to_string(p.proconfig, ';'), '-')
        FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace JOIN pg_language l ON l.oid = p.prolang
        WHERE {_SYS} AND p.oid < 16384 AND (p.prosecdef OR p.proconfig IS NOT NULL)"""),
    ("parameteracl", f"""SELECT pa.parname || ' ' || {_G.format(g="a.grantee")} || ' ' || a.privilege_type FROM pg_parameter_acl pa, LATERAL aclexplode(pa.paracl) a"""),
    ("tablespace", "SELECT t.spcname || ' owner=' || t.spcowner::regrole FROM pg_tablespace t"),
    ("tablespaceacl", f"""SELECT t.spcname || ' ' || {_G.format(g="a.grantee")} || ' ' || a.privilege_type FROM pg_tablespace t,
        LATERAL aclexplode(COALESCE(t.spcacl, acldefault('t', t.spcowner))) a WHERE a.grantee <> t.spcowner"""),
    ("extension", "SELECT e.extname || ' schema=' || e.extnamespace::regnamespace || ' owner=' || e.extowner::regrole FROM pg_extension e"),
]


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def major(conn: psycopg.Connection) -> int:
    return int(conn.execute("SHOW server_version_num").fetchone()[0]) // 10000


_PER_MAJOR = re.compile(r"^\w+@(\d+) ")


def for_major(lines: Iterable[str], version: int) -> set[str]:
    """Drop the lines that belong to another PostgreSQL major (``kind@17 ...``); every other line applies to all majors."""
    return {x for x in lines if not (m := _PER_MAJOR.match(x)) or int(m.group(1)) == version}


def live_lines(conn: psycopg.Connection) -> list[str]:
    """The live surface. Runs in one read-only transaction under ``search_path = pg_catalog`` so deparsed text is stable."""
    out = []
    version = major(conn)
    with conn.transaction():
        conn.execute("SET LOCAL search_path = pg_catalog")
        folding = conn.execute("SELECT to_regclass('kernel.rls_registry') IS NOT NULL AND to_regclass('kernel.settings') IS NOT NULL").fetchone()[0]
        admins_ok = conn.execute("SELECT to_regclass('kernel.settings') IS NOT NULL").fetchone()[0]
        for kind, text in QUERIES:
            if kind == "settings" and not admins_ok:
                continue
            sql = text if folding else text.replace(_FOLD_SELECT, "SELECT NULL::oid WHERE false")
            query = sql if admins_ok else sql.replace("COALESCE((SELECT admins FROM kernel.settings), '{}')", "'{}'::name[]")
            for (line,) in conn.execute(query):
                out.append(f"{kind}@{version} {line}" if kind == "sysfuncs" else f"{kind} {line}")
    if _managed(conn):                           # a known exception on managed platforms: reported in kernel.rls_warnings, not compared
        out = [x for x in out if not x.startswith("lofunctions ")]
    return sorted(normalise(out, conn.execute("SELECT rolname FROM pg_roles WHERE oid = 10").fetchone()[0]))


def normalise(lines: Iterable[str], superuser: str) -> set[str]:
    """Replace the bootstrap superuser's name (whatever the deployment calls it) by a fixed token."""
    token = re.compile(r'(?<![\w"])' + re.escape(superuser) + r'(?![\w"])')
    return {token.sub(BOOTSTRAP_SUPERUSER, x) for x in lines}


def _managed(conn: psycopg.Connection) -> bool:
    return bool(conn.execute("SELECT to_regclass('platform.deployment_profile') IS NOT NULL AND COALESCE("
                             "(SELECT (profile->>'managed')::boolean FROM platform.deployment_profile), false)").fetchone()[0])


def expected_files(extra: Iterable[str] = ()) -> list[Path]:
    """``surface*.txt`` beside this module, ``allowlist.yaml`` in every extra migration location, and the files given."""
    found = set(DB_DIR.glob("surface*.txt"))
    found |= {f for d in extra_locations() if (f := Path(d) / "allowlist.yaml").is_file()}
    found |= {Path(p) for p in extra if p}
    return sorted(found)


def _yaml_items(text: str, section: str = "surface") -> list[str]:
    """The only YAML an allowlist uses: ``surface:`` (lines to expect) and ``remove:`` (lines of the base state it replaces, for
    example the old definition of a function whose body it changes), each followed by ``- <line>`` items (quotes optional)."""
    out = []
    current = None
    for raw in text.splitlines():
        item = raw.strip()
        if item.endswith(":") and not raw.startswith((" ", "-")):
            current = item[:-1]
        elif current == section and item.startswith("- "):
            item = item[2:].strip()
            if len(item) > 1 and item[0] == item[-1] and item[0] in "'\"":
                item = item[1:-1].replace("''", "'") if item[0] == "'" else item[1:-1]
            out.append(item)
    return out


def expected_lines(files: Iterable[Path]) -> set[str]:
    out, removed = set(), set()
    for f in files:
        text = f.read_text(encoding="utf-8")
        if f.suffix in (".yaml", ".yml"):
            out |= set(_yaml_items(text))
            removed |= set(_yaml_items(text, "remove"))
        else:
            out |= {x for x in text.splitlines() if x and not x.startswith("#")}
    return out - removed


def diff(live: Iterable[str], expected: set[str]) -> list[tuple[str, str]]:
    live = set(live)
    return ([("50 surface: unexpected", line) for line in sorted(live - expected)]
            + [("50 surface: missing", line) for line in sorted(expected - live)])


def _window_roles(conn: psycopg.Connection) -> set[str]:
    """Roles an open restore window changes on purpose: the restore role and the application logins it switched off."""
    row = conn.execute("SELECT s.restore, w.disabled_logins FROM kernel.restore_window w, kernel.settings s").fetchone() \
        if conn.execute("SELECT to_regclass('kernel.restore_window') IS NOT NULL").fetchone()[0] else None
    return {row[0], *row[1]} if row else set()


def _migration_roles(conn: psycopg.Connection) -> set[str]:
    """While a document-store migration job is running its import role may be able to log in: the move scripts enable that login, and the
    reverse follower keeps it for as long as the bridge release runs. The job is started and ended by the migrate login alone."""
    if not conn.execute("SELECT to_regclass('platform.jobs') IS NOT NULL AND to_regclass('kernel.settings') IS NOT NULL").fetchone()[0]:
        return set()
    row = conn.execute("SELECT s.importer FROM kernel.settings s WHERE EXISTS "
                       "(SELECT 1 FROM platform.jobs j WHERE j.kind = 'docstore_migration' AND j.status = 'running')").fetchone() \
        if conn.execute("SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'kernel' AND table_name = 'settings' AND column_name = 'importer')").fetchone()[0] else None
    return {row[0]} if row else set()


def violations(conn: psycopg.Connection, extra: Iterable[str] = ()) -> list[tuple[str, str]]:
    """Diff of the live surface against the expected files. While a restore window is open only the role lines the window itself
    changes (the restore role, the switched-off logins) are left out of the comparison."""
    files = expected_files(extra)
    for f in files:
        log(f"surface: expected state from {f}")
    expected = for_major(expected_lines(files), major(conn))
    if _managed(conn):
        expected = {x for x in expected if not x.startswith("lofunctions ")}
    live = set(live_lines(conn))
    for role in _window_roles(conn) | _migration_roles(conn):
        live = {x for x in live if not x.startswith(f"role {role} ")}
        expected = {x for x in expected if not x.startswith(f"role {role} ")}
    return diff(live, expected)


def main() -> int:
    url = os.environ.get("PG_ADMIN_URL", "").replace("postgresql+psycopg://", "postgresql://")
    if not url:
        print("surface: PG_ADMIN_URL is not set", file=sys.stderr)
        return 2
    with psycopg.connect(url, autocommit=True) as conn:
        sys.stdout.write("\n".join(live_lines(conn)) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
