-- generated from the manifest by python -m db.lib.vetted render: edit the manifest, not this file
GRANT pipeshub_vetted_platform TO pipeshub_owner WITH SET TRUE;
GRANT USAGE ON SCHEMA platform TO pipeshub_vetted_platform;           -- the body names objects of its own schema
GRANT CREATE ON SCHEMA platform TO pipeshub_vetted_platform;
SET LOCAL ROLE pipeshub_vetted_platform;
CREATE FUNCTION platform.job_finish(p_id bigint, p_status text)
RETURNS boolean LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, platform, pg_temp
SET standard_conforming_strings = on
AS $fn$
BEGIN
  IF p_id IS NULL THEN RAISE EXCEPTION 'job_finish: p_id is required' USING ERRCODE = '22023'; END IF;
  IF p_status IS NULL THEN RAISE EXCEPTION 'job_finish: p_status is required' USING ERRCODE = '22023'; END IF;
  IF p_status NOT IN ('done', 'failed', 'cancelled') THEN RAISE EXCEPTION 'job_finish: p_status is not one of the allowed values' USING ERRCODE = '22023'; END IF;
  UPDATE platform.jobs SET status = p_status, updated_at = now() WHERE id = p_id AND status IN ('queued', 'running');
  RETURN FOUND;
END $fn$;
REVOKE ALL ON FUNCTION platform.job_finish(bigint, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION platform.job_finish(bigint, text) TO pipeshub_owner;
SET LOCAL ROLE pipeshub_owner;
REVOKE CREATE ON SCHEMA platform FROM pipeshub_vetted_platform;
REVOKE pipeshub_vetted_platform FROM pipeshub_owner;
SELECT kernel.register_function('platform.job_finish(bigint, text)', 'jobs', NULL, 'global', false);
