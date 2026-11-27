-- An extension schema may have its own vetted owner, the one the role bootstrap creates from the extra location's manifest
-- (db.bootstrap_roles.location_vetted_schemas). Its functions write only that schema (check 42 holds every vetted role to its own
-- schema); the kernel's vetted owner keeps SELECT only on the tables grant_vetted_owner_select names. The owner must be fresh: no
-- object, privilege or policy of its own in this database, a member of nothing, and only the owner role holding it (ADMIN, as the
-- bootstrap grants). A role used anywhere before, a schema not owned by the owner role, or one that already holds any object (a
-- relation, function, type or anything else that depends on the namespace), is
-- still refused. Core schemas are refused as before.
CREATE FUNCTION kernel._fresh_vetted_owner(p_role oid) RETURNS boolean
LANGUAGE sql STABLE SET search_path = pg_catalog, kernel, pg_temp AS $$
  SELECT NOT r.rolcanlogin AND NOT r.rolsuper AND NOT r.rolcreaterole AND NOT r.rolcreatedb AND NOT r.rolbypassrls AND NOT r.rolreplication
    AND NOT EXISTS (SELECT 1 FROM pg_shdepend d WHERE d.refclassid = 'pg_authid'::regclass AND d.refobjid = p_role
                    AND d.dbid IN (0, (SELECT oid FROM pg_database WHERE datname = current_database())))
    AND NOT EXISTS (SELECT 1 FROM pg_auth_members m WHERE m.member = p_role)
    AND NOT EXISTS (SELECT 1 FROM pg_auth_members m JOIN pg_roles o ON o.oid = m.member, kernel.settings s
                    WHERE m.roleid = p_role AND (o.rolname <> s.owner OR NOT m.admin_option))
  FROM pg_roles r WHERE r.oid = p_role $$;
REVOKE ALL ON FUNCTION kernel._fresh_vetted_owner(oid) FROM PUBLIC;

CREATE OR REPLACE FUNCTION kernel.declare_extension_schema(p_schema name) RETURNS void
LANGUAGE plpgsql SET search_path = pg_catalog, kernel, pg_temp AS $fn$
DECLARE s kernel.settings; v_owner oid;
BEGIN
  SELECT * INTO s FROM kernel.settings;
  IF p_schema IN ('kernel', 'platform', 'ext', 'public', 'information_schema') OR p_schema::text LIKE 'pg\_%' THEN
    RAISE EXCEPTION 'declare_extension_schema: % is not available to an extension', p_schema;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = p_schema) THEN RAISE EXCEPTION 'declare_extension_schema: no schema %', p_schema; END IF;
  SELECT r.oid INTO v_owner FROM pg_roles r WHERE r.rolname = s.vetted_prefix || p_schema;
  -- anything in the schema (a relation, function, type, operator, collation, ...) depends on its namespace
  IF EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = p_schema)
     OR EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = p_schema)
     OR EXISTS (SELECT 1 FROM pg_depend d JOIN pg_namespace n ON n.oid = d.refobjid
                WHERE d.refclassid = 'pg_namespace'::regclass AND n.nspname = p_schema)
     OR (SELECT n.nspowner FROM pg_namespace n WHERE n.nspname = p_schema) IS DISTINCT FROM (SELECT oid FROM pg_roles WHERE rolname = s.owner)
     OR (v_owner IS NOT NULL AND NOT kernel._fresh_vetted_owner(v_owner)) THEN
    RAISE EXCEPTION 'declare_extension_schema: % is not new (declare it before creating anything in it)', p_schema;
  END IF;
  INSERT INTO kernel.extension_schemas VALUES (p_schema) ON CONFLICT DO NOTHING;
END $fn$;
REVOKE ALL ON FUNCTION kernel.declare_extension_schema(name) FROM PUBLIC;
