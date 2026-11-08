-- The deployment profile epoch for the runtimes' start-up read (hand-written: the table is in schema platform, so there is no manifest
-- entry). The service roles hold no privilege on the table; they call this. SECURITY DEFINER, owner = the schema's vetted role.
GRANT pipeshub_vetted_kernel TO pipeshub_owner WITH SET TRUE;
GRANT USAGE ON SCHEMA kernel TO pipeshub_vetted_kernel;
GRANT CREATE ON SCHEMA kernel TO pipeshub_vetted_kernel;
SET LOCAL ROLE pipeshub_vetted_kernel;
CREATE FUNCTION kernel.profile_epoch() RETURNS int
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, kernel, pg_temp SET standard_conforming_strings = on AS $fn$
BEGIN
  RETURN (SELECT p.epoch FROM platform.deployment_profile p);
END $fn$;
REVOKE ALL ON FUNCTION kernel.profile_epoch() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION kernel.profile_epoch() TO pipeshub_node, pipeshub_connectors, pipeshub_indexing, pipeshub_query;
SET LOCAL ROLE pipeshub_owner;
REVOKE CREATE ON SCHEMA kernel FROM pipeshub_vetted_kernel;
REVOKE pipeshub_vetted_kernel FROM pipeshub_owner;
SELECT kernel.register_function('kernel.profile_epoch()', 'read', NULL, 'global', false);
