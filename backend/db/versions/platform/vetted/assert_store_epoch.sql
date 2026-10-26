-- The document store fence and its start-up reader (hand-written: the prologue class has no manifest entry). They live in schema
-- kernel beside kernel.assert_profile_epoch: SECURITY DEFINER, owned by the vetted owner of that schema, which reads the fence row
-- through the extension-read allowance. The service roles need EXECUTE and nothing on platform.store_epoch.
-- assert_store_epoch is VOLATILE and READ COMMITTED only (a REPEATABLE READ writer would pass on its old snapshot). It takes the
-- SHARED transaction-scoped barrier (class 7340 in kernel.lock_keys), held to commit: the cutover freeze takes the same key
-- exclusively in the transaction that sets the row read-only, waits for writers in flight, and every later writer gets 25006.
GRANT pipeshub_vetted_kernel TO pipeshub_owner WITH SET TRUE;
GRANT USAGE ON SCHEMA kernel TO pipeshub_vetted_kernel;
GRANT CREATE ON SCHEMA kernel TO pipeshub_vetted_kernel;
SET LOCAL ROLE pipeshub_vetted_kernel;
CREATE FUNCTION kernel.assert_store_epoch(p_epoch int) RETURNS void
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, kernel, pg_temp SET standard_conforming_strings = on AS $fn$
DECLARE r platform.store_epoch;
BEGIN
  IF current_setting('transaction_isolation') <> 'read committed' THEN
    RAISE EXCEPTION 'write units of work run at READ COMMITTED' USING ERRCODE = '25000';
  END IF;
  IF NOT pg_try_advisory_xact_lock_shared(7340, 1) THEN
    RAISE EXCEPTION 'the document store is frozen' USING ERRCODE = '25006';
  END IF;
  SELECT * INTO r FROM platform.store_epoch;
  IF r.backend IS DISTINCT FROM 'postgres' OR r.epoch IS DISTINCT FROM p_epoch THEN
    RAISE EXCEPTION 'the document store epoch does not match this process' USING ERRCODE = '55P04';
  END IF;
  IF r.readonly THEN
    RAISE EXCEPTION 'the document store is read-only' USING ERRCODE = '25006';
  END IF;
END $fn$;
-- The epoch a process starts with: NULL unless the row names postgres.
CREATE FUNCTION kernel.postgres_store_epoch() RETURNS int
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, kernel, pg_temp SET standard_conforming_strings = on AS $fn$
BEGIN
  RETURN (SELECT s.epoch FROM platform.store_epoch s WHERE s.backend = 'postgres');
END $fn$;
REVOKE ALL ON FUNCTION kernel.assert_store_epoch(int), kernel.postgres_store_epoch() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION kernel.assert_store_epoch(int) TO pipeshub_jobs;
GRANT EXECUTE ON FUNCTION kernel.postgres_store_epoch() TO pipeshub_node;
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pipeshub_node_login') THEN
    GRANT EXECUTE ON FUNCTION kernel.assert_store_epoch(int) TO pipeshub_node_login;
  END IF;
END $$;
SET LOCAL ROLE pipeshub_owner;
REVOKE CREATE ON SCHEMA kernel FROM pipeshub_vetted_kernel;
REVOKE pipeshub_vetted_kernel FROM pipeshub_owner;
SELECT kernel.register_function('kernel.assert_store_epoch(integer)', 'prologue');
SELECT kernel.register_function('kernel.postgres_store_epoch()', 'read', NULL, 'global', false);
