-- The unit-of-work fence (hand-written: the prologue class has no manifest entry). SECURITY DEFINER, owned by the schema's vetted
-- role, so the service logins need EXECUTE and nothing on the table it reads. VOLATILE (a STABLE call whose result is unused is
-- planned away) and READ COMMITTED only (a REPEATABLE READ writer would pass on its old snapshot). It takes the SHARED
-- transaction-scoped barrier lock (class registered in kernel.lock_keys), held to commit: the profile switch takes the same key
-- exclusively in the transaction that changes the epoch, waits for writers in flight, and every later writer is refused with 25006.
GRANT pipeshub_vetted_kernel TO pipeshub_owner WITH SET TRUE;
GRANT USAGE ON SCHEMA kernel TO pipeshub_vetted_kernel;
GRANT CREATE ON SCHEMA kernel TO pipeshub_vetted_kernel;
ALTER FUNCTION kernel.assert_profile_epoch(int) OWNER TO pipeshub_vetted_kernel;
SET LOCAL ROLE pipeshub_vetted_kernel;
CREATE OR REPLACE FUNCTION kernel.assert_profile_epoch(p_epoch int) RETURNS void
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, kernel, pg_temp SET standard_conforming_strings = on AS $fn$
DECLARE v_epoch int;
BEGIN
  IF current_setting('transaction_isolation') <> 'read committed' THEN
    RAISE EXCEPTION 'write units of work run at READ COMMITTED' USING ERRCODE = '25000';
  END IF;
  IF NOT pg_try_advisory_xact_lock_shared(7341, 1) THEN
    RAISE EXCEPTION 'the deployment profile is being switched' USING ERRCODE = '25006';
  END IF;
  SELECT epoch INTO v_epoch FROM platform.deployment_profile;
  IF v_epoch IS DISTINCT FROM p_epoch THEN
    RAISE EXCEPTION 'deployment profile epoch is %, this process expects %', v_epoch, p_epoch USING ERRCODE = '55000';
  END IF;
END $fn$;
-- A database migrated at kernel_0010 before the vetted functions pinned this setting gets it here (idempotent on a fresh install).
ALTER FUNCTION kernel.org_lineage(text) SET standard_conforming_strings = on;
ALTER FUNCTION kernel.org_parent_ids(text) SET standard_conforming_strings = on;
SET LOCAL ROLE pipeshub_owner;
REVOKE CREATE ON SCHEMA kernel FROM pipeshub_vetted_kernel;
REVOKE pipeshub_vetted_kernel FROM pipeshub_owner;
