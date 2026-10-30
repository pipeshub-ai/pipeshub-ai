-- generated from the manifest by python -m db.lib.vetted render: edit the manifest, not this file
GRANT pipeshub_vetted_platform TO pipeshub_owner WITH SET TRUE;
GRANT USAGE ON SCHEMA platform TO pipeshub_vetted_platform;           -- the body names objects of its own schema
GRANT CREATE ON SCHEMA platform TO pipeshub_vetted_platform;
SET LOCAL ROLE pipeshub_vetted_platform;
CREATE FUNCTION platform.job_checkpoint(p_id bigint, p_progress text)
RETURNS boolean LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, platform, pg_temp
SET standard_conforming_strings = on
AS $fn$
BEGIN
  IF p_id IS NULL THEN RAISE EXCEPTION 'job_checkpoint: p_id is required' USING ERRCODE = '22023'; END IF;
  IF p_progress IS NULL THEN RAISE EXCEPTION 'job_checkpoint: p_progress is required' USING ERRCODE = '22023'; END IF;
  IF length(p_progress) NOT BETWEEN 2 AND 65536 THEN RAISE EXCEPTION 'job_checkpoint: p_progress length must be 2..65536' USING ERRCODE = '22023'; END IF;
  UPDATE platform.jobs SET progress = p_progress::jsonb, updated_at = now() WHERE id = p_id AND status = 'running';
  RETURN FOUND;
END $fn$;
REVOKE ALL ON FUNCTION platform.job_checkpoint(bigint, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION platform.job_checkpoint(bigint, text) TO pipeshub_jobs;
SET LOCAL ROLE pipeshub_owner;
REVOKE CREATE ON SCHEMA platform FROM pipeshub_vetted_platform;
REVOKE pipeshub_vetted_platform FROM pipeshub_owner;
SELECT kernel.register_function('platform.job_checkpoint(bigint, text)', 'jobs', NULL, 'global', false);
