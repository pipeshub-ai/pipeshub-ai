-- generated from the manifest by python -m db.lib.vetted render: edit the manifest, not this file
GRANT pipeshub_vetted_identity TO pipeshub_owner WITH SET TRUE;
GRANT USAGE ON SCHEMA identity TO pipeshub_vetted_identity;           -- the body names objects of its own schema
GRANT CREATE ON SCHEMA identity TO pipeshub_vetted_identity;
SET LOCAL ROLE pipeshub_vetted_identity;
CREATE FUNCTION identity.gauge_live_orgs()
RETURNS TABLE (org_id text, account_type text, domain text) LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, identity, pg_temp
SET standard_conforming_strings = on
AS $fn$
BEGIN

  RETURN QUERY SELECT o.org_id, o.account_type, o.domain FROM identity.orgs o WHERE NOT o.is_deleted;
END $fn$;
REVOKE ALL ON FUNCTION identity.gauge_live_orgs() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION identity.gauge_live_orgs() TO pipeshub_jobs;
SET LOCAL ROLE pipeshub_owner;
REVOKE CREATE ON SCHEMA identity FROM pipeshub_vetted_identity;
REVOKE pipeshub_vetted_identity FROM pipeshub_owner;
SELECT kernel.register_function('identity.gauge_live_orgs()', 'jobs', NULL, 'global', false);
