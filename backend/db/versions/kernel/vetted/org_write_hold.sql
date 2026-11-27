-- The two write fences (hand-written: the prologue class has no manifest entry) also refuse a unit whose org's writes are held
-- (kernel.org_writes_held, an extension point; here it holds none). The unit's org is app.org_id, which the prologue sets in its
-- materialized pre subquery, before the subquery that calls the fence; a unit with no org is never held. The hold is asked after
-- the fence holds its install-wide barrier (7341 here, 7340 for the document store), so whoever records a hold takes both
-- exclusively in that transaction: it waits for every write unit in flight and refuses new ones until it commits, and every fence
-- that runs after it sees the hold. No per-org lock: a hold is rare, a write unit is not. Everything else is the bodies of
-- kernel_0011 and platform_0001.
GRANT pipeshub_vetted_kernel TO pipeshub_owner WITH SET TRUE;
GRANT USAGE ON SCHEMA kernel TO pipeshub_vetted_kernel;
GRANT CREATE ON SCHEMA kernel TO pipeshub_vetted_kernel;
SET LOCAL ROLE pipeshub_vetted_kernel;
CREATE OR REPLACE FUNCTION kernel.assert_profile_epoch(p_epoch int) RETURNS void
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, kernel, pg_temp SET standard_conforming_strings = on AS $fn$
DECLARE v_epoch int; v_org text := NULLIF(current_setting('app.org_id', true), '');
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
  IF v_org IS NOT NULL AND kernel.org_writes_held(v_org) THEN
    RAISE EXCEPTION 'the organisation''s writes are held' USING ERRCODE = '25006';
  END IF;
END $fn$;
CREATE OR REPLACE FUNCTION kernel.assert_store_epoch(p_epoch int) RETURNS void
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, kernel, pg_temp SET standard_conforming_strings = on AS $fn$
DECLARE r platform.store_epoch; v_org text := NULLIF(current_setting('app.org_id', true), '');
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
  IF v_org IS NOT NULL AND kernel.org_writes_held(v_org) THEN
    RAISE EXCEPTION 'the organisation''s writes are held' USING ERRCODE = '25006';
  END IF;
END $fn$;
SET LOCAL ROLE pipeshub_owner;
REVOKE CREATE ON SCHEMA kernel FROM pipeshub_vetted_kernel;
REVOKE pipeshub_vetted_kernel FROM pipeshub_owner;
