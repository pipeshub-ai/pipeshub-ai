-- The deployment profile epoch. Write units of work call kernel.assert_profile_epoch(n) so that, while a
-- profile is being switched, processes started with the old epoch fail their writes instead of writing
-- to the wrong store. Schema platform is shared with the platform branch (its first revision also uses
-- IF NOT EXISTS).
CREATE SCHEMA IF NOT EXISTS platform;

CREATE TABLE platform.deployment_profile (
  epoch   int NOT NULL,
  profile jsonb NOT NULL DEFAULT '{}');
CREATE UNIQUE INDEX deployment_profile_one_row ON platform.deployment_profile ((true));
INSERT INTO platform.deployment_profile (epoch) VALUES (1);

CREATE FUNCTION kernel.assert_profile_epoch(p_epoch int) RETURNS void
LANGUAGE plpgsql STABLE SET search_path = pg_catalog, platform, pg_temp AS $$
DECLARE v_epoch int;
BEGIN
  SELECT epoch INTO v_epoch FROM platform.deployment_profile;
  IF v_epoch IS DISTINCT FROM p_epoch THEN
    RAISE EXCEPTION 'deployment profile epoch is %, this process expects %', v_epoch, p_epoch
      USING ERRCODE = '55000';
  END IF;
END $$;

REVOKE ALL ON FUNCTION kernel.assert_profile_epoch(int) FROM PUBLIC;
GRANT USAGE ON SCHEMA platform TO pipeshub_node, pipeshub_connectors, pipeshub_indexing, pipeshub_query;
GRANT SELECT ON platform.deployment_profile TO pipeshub_node, pipeshub_connectors, pipeshub_indexing, pipeshub_query;
GRANT EXECUTE ON FUNCTION kernel.assert_profile_epoch(int) TO pipeshub_node, pipeshub_connectors, pipeshub_indexing, pipeshub_query;
