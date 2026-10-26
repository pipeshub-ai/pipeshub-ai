-- A STABLE function whose result is not referenced is planned away and never runs. The fence must be VOLATILE, and it
-- only means something under READ COMMITTED (a REPEATABLE READ writer passes on its old snapshot), so it asserts that too.
CREATE OR REPLACE FUNCTION kernel.assert_profile_epoch(p_epoch int) RETURNS void
LANGUAGE plpgsql VOLATILE SET search_path = pg_catalog, platform, pg_temp AS $$
DECLARE v_epoch int;
BEGIN
  IF current_setting('transaction_isolation') <> 'read committed' THEN
    RAISE EXCEPTION 'write units of work run at READ COMMITTED' USING ERRCODE = '25000';
  END IF;
  SELECT epoch INTO v_epoch FROM platform.deployment_profile;
  IF v_epoch IS DISTINCT FROM p_epoch THEN
    RAISE EXCEPTION 'deployment profile epoch is %, this process expects %', v_epoch, p_epoch USING ERRCODE = '55000';
  END IF;
END $$;
SELECT kernel.register_function('kernel.assert_profile_epoch(integer)', 'prologue');

-- The unit of work parses the prologue statement before it switches role, so each service LOGIN needs USAGE on the fence's
-- schema and EXECUTE on the fence, and nothing else. (Missing logins are skipped: the bootstrap creates them.)
DO $$
DECLARE l text;
BEGIN
  FOREACH l IN ARRAY ARRAY['pipeshub_node_login', 'pipeshub_connectors_login', 'pipeshub_indexing_login', 'pipeshub_query_login'] LOOP
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = l) THEN
      EXECUTE format('GRANT USAGE ON SCHEMA kernel TO %I', l);
      EXECUTE format('GRANT EXECUTE ON FUNCTION kernel.assert_profile_epoch(int) TO %I', l);
    END IF;
  END LOOP;
END $$;
