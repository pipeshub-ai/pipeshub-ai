-- generated from the manifest by python -m db.lib.vetted render: edit the manifest, not this file
GRANT pipeshub_vetted_platform TO pipeshub_owner WITH SET TRUE;
GRANT USAGE ON SCHEMA platform TO pipeshub_vetted_platform;           -- the body names objects of its own schema
GRANT CREATE ON SCHEMA platform TO pipeshub_vetted_platform;
SET LOCAL ROLE pipeshub_vetted_platform;
CREATE FUNCTION platform.job_start(p_kind text)
RETURNS bigint LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, platform, pg_temp
SET standard_conforming_strings = on
AS $fn$
BEGIN
  IF p_kind IS NULL THEN RAISE EXCEPTION 'job_start: p_kind is required' USING ERRCODE = '22023'; END IF;
  IF p_kind !~ '^[a-z][a-z_]{2,39}$' THEN RAISE EXCEPTION 'job_start: p_kind does not match the expected format' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM platform.jobs j WHERE j.kind = p_kind AND j.status = 'running') THEN
    RAISE EXCEPTION 'a job of this kind is already running' USING ERRCODE = '55006';
  END IF;
  BEGIN
    INSERT INTO platform.jobs (kind, status) VALUES (p_kind, 'running');
    -- the unique index admits one running job of a kind, so the row found is the one this call inserted
    RETURN (SELECT j.id FROM platform.jobs j WHERE j.kind = p_kind AND j.status = 'running');
  EXCEPTION WHEN unique_violation THEN
    RAISE EXCEPTION 'a job of this kind is already running' USING ERRCODE = '55006';
  END;
END $fn$;
REVOKE ALL ON FUNCTION platform.job_start(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION platform.job_start(text) TO pipeshub_owner;
SET LOCAL ROLE pipeshub_owner;
REVOKE CREATE ON SCHEMA platform FROM pipeshub_vetted_platform;
REVOKE pipeshub_vetted_platform FROM pipeshub_owner;
SELECT kernel.register_function('platform.job_start(text)', 'jobs', NULL, 'global', false);
