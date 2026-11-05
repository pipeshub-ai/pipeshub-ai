-- generated from the manifest by python -m db.lib.vetted render: edit the manifest, not this file
GRANT pipeshub_vetted_kg TO pipeshub_owner WITH SET TRUE;
GRANT USAGE ON SCHEMA kg TO pipeshub_vetted_kg;           -- the body names objects of its own schema
GRANT CREATE ON SCHEMA kg TO pipeshub_vetted_kg;
SET LOCAL ROLE pipeshub_vetted_kg;
CREATE FUNCTION kg.w_queue_touch(p_id bigint, p_seen bigint, p_worker text, p_attempts int, p_cursor text)
RETURNS bigint LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, kg, pg_temp
SET standard_conforming_strings = on
SET plan_cache_mode = force_custom_plan
AS $fn$
DECLARE v_org text := NULLIF(current_setting('app.org_id', true), '');
BEGIN
  IF p_id IS NULL THEN RAISE EXCEPTION 'w_queue_touch: p_id is required' USING ERRCODE = '22023'; END IF;
  IF p_id NOT BETWEEN 1 AND 9223372036854775807 THEN RAISE EXCEPTION 'w_queue_touch: p_id must be 1..9223372036854775807' USING ERRCODE = '22023'; END IF;
  IF p_seen IS NULL THEN RAISE EXCEPTION 'w_queue_touch: p_seen is required' USING ERRCODE = '22023'; END IF;
  IF p_seen NOT BETWEEN 0 AND 9223372036854775807 THEN RAISE EXCEPTION 'w_queue_touch: p_seen must be 0..9223372036854775807' USING ERRCODE = '22023'; END IF;
  IF p_worker IS NULL THEN RAISE EXCEPTION 'w_queue_touch: p_worker is required' USING ERRCODE = '22023'; END IF;
  IF length(p_worker) NOT BETWEEN 1 AND 128 THEN RAISE EXCEPTION 'w_queue_touch: p_worker length must be 1..128' USING ERRCODE = '22023'; END IF;
  IF p_attempts IS NULL THEN RAISE EXCEPTION 'w_queue_touch: p_attempts is required' USING ERRCODE = '22023'; END IF;
  IF p_attempts NOT BETWEEN 0 AND 1000000 THEN RAISE EXCEPTION 'w_queue_touch: p_attempts must be 0..1000000' USING ERRCODE = '22023'; END IF;
  IF p_cursor IS NOT NULL AND length(p_cursor) NOT BETWEEN 0 AND 512 THEN RAISE EXCEPTION 'w_queue_touch: p_cursor length must be 0..512' USING ERRCODE = '22023'; END IF;
  IF v_org IS NULL THEN RETURN 0; END IF;       -- no org (or someone else's): no rows, the same answer
  DECLARE v_beat bigint; v_n integer;
  BEGIN
    v_beat := GREATEST(floor(extract(epoch FROM clock_timestamp()) * 1000), p_seen + 1);
    UPDATE kg.restamp_queue t SET claimed_by = p_worker, heartbeat_ms = v_beat, attempts = p_attempts, cursor = p_cursor WHERE t.org_id = v_org AND t.id = p_id AND t.heartbeat_ms = p_seen;
    GET DIAGNOSTICS v_n = ROW_COUNT;
    IF v_n = 1 THEN
      RETURN v_beat;
    END IF;
    RETURN NULL;
  END;
END $fn$;
REVOKE ALL ON FUNCTION kg.w_queue_touch(bigint, bigint, text, int, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION kg.w_queue_touch(bigint, bigint, text, int, text) TO pipeshub_connectors;
SET LOCAL ROLE pipeshub_owner;
REVOKE CREATE ON SCHEMA kg FROM pipeshub_vetted_kg;
REVOKE pipeshub_vetted_kg FROM pipeshub_owner;
SELECT kernel.register_function('kg.w_queue_touch(bigint, bigint, text, int, text)', 'write', 'pipeshub_connectors', 'org', false);
SELECT kernel.allow_vetted_write_op('kg.w_queue_touch(bigint, bigint, text, int, text)', 'kg.restamp_queue', 'update', NULL, NULL, NULL, 5000);
