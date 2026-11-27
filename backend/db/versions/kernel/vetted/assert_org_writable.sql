-- generated from the manifest by python -m db.lib.vetted render: edit the manifest, not this file
GRANT pipeshub_vetted_kernel TO pipeshub_owner WITH SET TRUE;
GRANT USAGE ON SCHEMA kernel TO pipeshub_vetted_kernel;           -- the body names objects of its own schema
GRANT CREATE ON SCHEMA kernel TO pipeshub_vetted_kernel;
SET LOCAL ROLE pipeshub_vetted_kernel;
CREATE FUNCTION kernel.assert_org_writable(p_org text)
RETURNS boolean LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, kernel, pg_temp
SET standard_conforming_strings = on
AS $fn$
BEGIN
  IF p_org IS NULL THEN RAISE EXCEPTION 'assert_org_writable: p_org is required' USING ERRCODE = '22023'; END IF;
  IF length(p_org) NOT BETWEEN 1 AND 64 THEN RAISE EXCEPTION 'assert_org_writable: p_org length must be 1..64' USING ERRCODE = '22023'; END IF;
  IF NOT pg_try_advisory_xact_lock_shared(7341, 1) THEN
    RAISE EXCEPTION 'the organisation writes are being held' USING ERRCODE = '25006';
  END IF;
  IF kernel.org_writes_held(p_org) THEN
    RAISE EXCEPTION 'the organisation writes are held' USING ERRCODE = '25006';
  END IF;
  RETURN true;
END $fn$;
REVOKE ALL ON FUNCTION kernel.assert_org_writable(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION kernel.assert_org_writable(text) TO pipeshub_jobs;
SET LOCAL ROLE pipeshub_owner;
REVOKE CREATE ON SCHEMA kernel FROM pipeshub_vetted_kernel;
REVOKE pipeshub_vetted_kernel FROM pipeshub_owner;
SELECT kernel.register_function('kernel.assert_org_writable(text)', 'jobs', NULL, 'global', false);
