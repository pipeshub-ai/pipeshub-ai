-- generated from the manifest by python -m db.lib.vetted render: edit the manifest, not this file
GRANT pipeshub_vetted_identity TO pipeshub_owner WITH SET TRUE;
GRANT USAGE ON SCHEMA identity TO pipeshub_vetted_identity;           -- the body names objects of its own schema
GRANT CREATE ON SCHEMA identity TO pipeshub_vetted_identity;
SET LOCAL ROLE pipeshub_vetted_identity;
CREATE FUNCTION identity.org_count(p_live_only boolean)
RETURNS bigint LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, identity, pg_temp
SET standard_conforming_strings = on
AS $fn$
BEGIN
  IF p_live_only IS NULL THEN RAISE EXCEPTION 'org_count: p_live_only is required' USING ERRCODE = '22023'; END IF;
  RETURN (SELECT count(*) FROM identity.orgs o WHERE NOT p_live_only OR NOT o.is_deleted);
END $fn$;
REVOKE ALL ON FUNCTION identity.org_count(boolean) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION identity.org_count(boolean) TO pipeshub_node;
SET LOCAL ROLE pipeshub_owner;
REVOKE CREATE ON SCHEMA identity FROM pipeshub_vetted_identity;
REVOKE pipeshub_vetted_identity FROM pipeshub_owner;
SELECT kernel.register_function('identity.org_count(boolean)', 'read', NULL, 'global', false);
