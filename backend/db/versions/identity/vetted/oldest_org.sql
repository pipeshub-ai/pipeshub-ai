-- generated from the manifest by python -m db.lib.vetted render: edit the manifest, not this file
GRANT pipeshub_vetted_identity TO pipeshub_owner WITH SET TRUE;
GRANT USAGE ON SCHEMA identity TO pipeshub_vetted_identity;           -- the body names objects of its own schema
GRANT CREATE ON SCHEMA identity TO pipeshub_vetted_identity;
SET LOCAL ROLE pipeshub_vetted_identity;
CREATE FUNCTION identity.oldest_org()
RETURNS text LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, identity, pg_temp
SET standard_conforming_strings = on
AS $fn$
BEGIN

  RETURN (SELECT o.org_id FROM identity.orgs o WHERE NOT o.is_deleted ORDER BY o.created_at, o.org_id LIMIT 1);
END $fn$;
REVOKE ALL ON FUNCTION identity.oldest_org() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION identity.oldest_org() TO pipeshub_node;
SET LOCAL ROLE pipeshub_owner;
REVOKE CREATE ON SCHEMA identity FROM pipeshub_vetted_identity;
REVOKE pipeshub_vetted_identity FROM pipeshub_owner;
SELECT kernel.register_function('identity.oldest_org()', 'read', NULL, 'global', false);
