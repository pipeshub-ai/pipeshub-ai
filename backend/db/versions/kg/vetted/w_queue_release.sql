-- generated from the manifest by python -m db.lib.vetted render: edit the manifest, not this file
GRANT pipeshub_vetted_kg TO pipeshub_owner WITH SET TRUE;
GRANT USAGE ON SCHEMA kg TO pipeshub_vetted_kg;           -- the body names objects of its own schema
GRANT CREATE ON SCHEMA kg TO pipeshub_vetted_kg;
SET LOCAL ROLE pipeshub_vetted_kg;
CREATE FUNCTION kg.w_queue_release(p_id bigint, p_worker text)
RETURNS boolean LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, kg, pg_temp
SET standard_conforming_strings = on
SET plan_cache_mode = force_custom_plan
AS $fn$
DECLARE v_org text := NULLIF(current_setting('app.org_id', true), '');
BEGIN
  IF p_id IS NULL THEN RAISE EXCEPTION 'w_queue_release: p_id is required' USING ERRCODE = '22023'; END IF;
  IF p_id NOT BETWEEN 1 AND 9223372036854775807 THEN RAISE EXCEPTION 'w_queue_release: p_id must be 1..9223372036854775807' USING ERRCODE = '22023'; END IF;
  IF p_worker IS NULL THEN RAISE EXCEPTION 'w_queue_release: p_worker is required' USING ERRCODE = '22023'; END IF;
  IF length(p_worker) NOT BETWEEN 1 AND 128 THEN RAISE EXCEPTION 'w_queue_release: p_worker length must be 1..128' USING ERRCODE = '22023'; END IF;
  IF v_org IS NULL THEN RETURN false; END IF;       -- no org (or someone else's): no rows, the same answer
  DECLARE v_n integer;
  BEGIN
    UPDATE kg.restamp_queue t SET claimed_by = NULL WHERE t.org_id = v_org AND t.id = p_id AND t.claimed_by = p_worker;
    GET DIAGNOSTICS v_n = ROW_COUNT;
    RETURN v_n = 1;
  END;
END $fn$;
REVOKE ALL ON FUNCTION kg.w_queue_release(bigint, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION kg.w_queue_release(bigint, text) TO pipeshub_connectors;
SET LOCAL ROLE pipeshub_owner;
REVOKE CREATE ON SCHEMA kg FROM pipeshub_vetted_kg;
REVOKE pipeshub_vetted_kg FROM pipeshub_owner;
SELECT kernel.register_function('kg.w_queue_release(bigint, text)', 'write', 'pipeshub_connectors', 'org', false);
SELECT kernel.allow_vetted_write_op('kg.w_queue_release(bigint, text)', 'kg.restamp_queue', 'update', NULL, NULL, NULL, 5000);
