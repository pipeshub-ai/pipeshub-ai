-- generated from the manifest by python -m db.lib.vetted render: edit the manifest, not this file
GRANT pipeshub_vetted_kg TO pipeshub_owner WITH SET TRUE;
GRANT USAGE ON SCHEMA kg TO pipeshub_vetted_kg;           -- the body names objects of its own schema
GRANT CREATE ON SCHEMA kg TO pipeshub_vetted_kg;
SET LOCAL ROLE pipeshub_vetted_kg;
CREATE FUNCTION kg.w_queue_add(p_id bigint, p_app_sk bigint, p_kind text, p_reason text, p_fenced boolean)
RETURNS boolean LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, kg, pg_temp
SET standard_conforming_strings = on
SET plan_cache_mode = force_custom_plan
AS $fn$
DECLARE v_org text := NULLIF(current_setting('app.org_id', true), '');
BEGIN
  IF p_id IS NULL THEN RAISE EXCEPTION 'w_queue_add: p_id is required' USING ERRCODE = '22023'; END IF;
  IF p_id NOT BETWEEN 1 AND 9223372036854775807 THEN RAISE EXCEPTION 'w_queue_add: p_id must be 1..9223372036854775807' USING ERRCODE = '22023'; END IF;
  IF p_app_sk IS NULL THEN RAISE EXCEPTION 'w_queue_add: p_app_sk is required' USING ERRCODE = '22023'; END IF;
  IF p_app_sk NOT BETWEEN 0 AND 9223372036854775807 THEN RAISE EXCEPTION 'w_queue_add: p_app_sk must be 0..9223372036854775807' USING ERRCODE = '22023'; END IF;
  IF p_kind IS NULL THEN RAISE EXCEPTION 'w_queue_add: p_kind is required' USING ERRCODE = '22023'; END IF;
  IF p_kind NOT IN ('rows', 'app') THEN RAISE EXCEPTION 'w_queue_add: p_kind is not one of the allowed values' USING ERRCODE = '22023'; END IF;
  IF p_reason IS NULL THEN RAISE EXCEPTION 'w_queue_add: p_reason is required' USING ERRCODE = '22023'; END IF;
  IF length(p_reason) NOT BETWEEN 1 AND 64 THEN RAISE EXCEPTION 'w_queue_add: p_reason length must be 1..64' USING ERRCODE = '22023'; END IF;
  IF p_fenced IS NULL THEN RAISE EXCEPTION 'w_queue_add: p_fenced is required' USING ERRCODE = '22023'; END IF;
  IF v_org IS NULL THEN RETURN false; END IF;       -- no org (or someone else's): no rows, the same answer
  DECLARE v_now bigint;
  BEGIN
    v_now := floor(extract(epoch FROM clock_timestamp()) * 1000);
    INSERT INTO kg.restamp_queue (org_id, id, app_sk, kind, reason, fenced, enqueued_ms) VALUES (v_org, p_id, p_app_sk, p_kind, p_reason, p_fenced, v_now)
      ON CONFLICT (org_id, id) DO NOTHING;
    RETURN true;
  END;
END $fn$;
REVOKE ALL ON FUNCTION kg.w_queue_add(bigint, bigint, text, text, boolean) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION kg.w_queue_add(bigint, bigint, text, text, boolean) TO pipeshub_connectors;
SET LOCAL ROLE pipeshub_owner;
REVOKE CREATE ON SCHEMA kg FROM pipeshub_vetted_kg;
REVOKE pipeshub_vetted_kg FROM pipeshub_owner;
SELECT kernel.register_function('kg.w_queue_add(bigint, bigint, text, text, boolean)', 'write', 'pipeshub_connectors', 'org', false);
SELECT kernel.allow_vetted_write_op('kg.w_queue_add(bigint, bigint, text, text, boolean)', 'kg.restamp_queue', 'upsert', NULL, ARRAY[]::name[], NULL, 5000);
