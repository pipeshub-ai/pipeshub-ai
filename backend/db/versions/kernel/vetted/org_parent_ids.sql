-- generated from the manifest by python -m db.lib.vetted render: edit the manifest, not this file
GRANT pipeshub_vetted_kernel TO pipeshub_owner WITH SET TRUE;
GRANT USAGE ON SCHEMA kernel TO pipeshub_vetted_kernel;           -- the body names objects of its own schema
GRANT CREATE ON SCHEMA kernel TO pipeshub_vetted_kernel;
SET LOCAL ROLE pipeshub_vetted_kernel;
CREATE FUNCTION kernel.org_parent_ids(p_org text)
RETURNS text[] LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, kernel, pg_temp
SET standard_conforming_strings = on
AS $fn$
BEGIN
  IF p_org IS NULL THEN RAISE EXCEPTION 'org_parent_ids: p_org is required' USING ERRCODE = '22023'; END IF;
  IF p_org !~ '^[0-9a-f]{24}$' THEN RAISE EXCEPTION 'org_parent_ids: p_org does not match the expected format' USING ERRCODE = '22023'; END IF;
  RETURN '{}';
END $fn$;
REVOKE ALL ON FUNCTION kernel.org_parent_ids(text) FROM PUBLIC;
SET LOCAL ROLE pipeshub_owner;
REVOKE CREATE ON SCHEMA kernel FROM pipeshub_vetted_kernel;
REVOKE pipeshub_vetted_kernel FROM pipeshub_owner;
SELECT kernel.register_function('kernel.org_parent_ids(text)', 'read', NULL, 'helper', false);
