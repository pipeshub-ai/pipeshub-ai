-- generated from the manifest by python -m db.lib.vetted render: edit the manifest, not this file
GRANT pipeshub_vetted_identity TO pipeshub_owner WITH SET TRUE;
GRANT USAGE ON SCHEMA identity TO pipeshub_vetted_identity;           -- the body names objects of its own schema
GRANT CREATE ON SCHEMA identity TO pipeshub_vetted_identity;
SET LOCAL ROLE pipeshub_vetted_identity;
CREATE FUNCTION identity.gauge_auth_configs()
RETURNS TABLE (org_id text, auth_steps jsonb) LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, identity, pg_temp
SET standard_conforming_strings = on
AS $fn$
BEGIN

  RETURN QUERY SELECT c.org_id, c.auth_steps FROM identity.org_auth_config c WHERE NOT c.is_deleted;
END $fn$;
REVOKE ALL ON FUNCTION identity.gauge_auth_configs() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION identity.gauge_auth_configs() TO pipeshub_jobs;
SET LOCAL ROLE pipeshub_owner;
REVOKE CREATE ON SCHEMA identity FROM pipeshub_vetted_identity;
REVOKE pipeshub_vetted_identity FROM pipeshub_owner;
SELECT kernel.register_function('identity.gauge_auth_configs()', 'jobs', NULL, 'global', false);
