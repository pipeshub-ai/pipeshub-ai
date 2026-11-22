-- generated from the manifest by python -m db.lib.vetted render: edit the manifest, not this file
GRANT pipeshub_vetted_kg TO pipeshub_owner WITH SET TRUE;
GRANT USAGE ON SCHEMA kg TO pipeshub_vetted_kg;           -- the body names objects of its own schema
GRANT CREATE ON SCHEMA kg TO pipeshub_vetted_kg;
SET LOCAL ROLE pipeshub_vetted_kg;
CREATE FUNCTION kg.w_projection_set(p_app_sk bigint, p_code_epoch int, p_mode text, p_digest text, p_cursor text, p_verified_at timestamptz)
RETURNS boolean LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, kg, pg_temp
SET standard_conforming_strings = on
SET plan_cache_mode = force_custom_plan
AS $fn$
DECLARE v_org text := NULLIF(current_setting('app.org_id', true), '');
BEGIN
  IF p_app_sk IS NULL THEN RAISE EXCEPTION 'w_projection_set: p_app_sk is required' USING ERRCODE = '22023'; END IF;
  IF p_app_sk NOT BETWEEN 0 AND 9223372036854775807 THEN RAISE EXCEPTION 'w_projection_set: p_app_sk must be 0..9223372036854775807' USING ERRCODE = '22023'; END IF;
  IF p_code_epoch IS NULL THEN RAISE EXCEPTION 'w_projection_set: p_code_epoch is required' USING ERRCODE = '22023'; END IF;
  IF p_code_epoch NOT BETWEEN 0 AND 1000000 THEN RAISE EXCEPTION 'w_projection_set: p_code_epoch must be 0..1000000' USING ERRCODE = '22023'; END IF;
  IF p_mode IS NULL THEN RAISE EXCEPTION 'w_projection_set: p_mode is required' USING ERRCODE = '22023'; END IF;
  IF p_mode NOT IN ('legacy', 'shadow', 'enforce') THEN RAISE EXCEPTION 'w_projection_set: p_mode is not one of the allowed values' USING ERRCODE = '22023'; END IF;
  IF p_digest IS NOT NULL AND length(p_digest) NOT BETWEEN 0 AND 128 THEN RAISE EXCEPTION 'w_projection_set: p_digest length must be 0..128' USING ERRCODE = '22023'; END IF;
  IF p_cursor IS NOT NULL AND length(p_cursor) NOT BETWEEN 0 AND 512 THEN RAISE EXCEPTION 'w_projection_set: p_cursor length must be 0..512' USING ERRCODE = '22023'; END IF;
  IF v_org IS NULL THEN RETURN false; END IF;       -- no org (or someone else's): no rows, the same answer
  INSERT INTO kg.projection_state (org_id, app_sk, code_epoch, mode, verify_digest, backfill_cursor, verified_at) VALUES (v_org, p_app_sk, p_code_epoch, p_mode, p_digest, p_cursor, p_verified_at)
      ON CONFLICT (org_id, app_sk) DO UPDATE SET code_epoch = excluded.code_epoch, mode = excluded.mode, verify_digest = excluded.verify_digest, backfill_cursor = excluded.backfill_cursor, verified_at = excluded.verified_at;
    RETURN true;
END $fn$;
REVOKE ALL ON FUNCTION kg.w_projection_set(bigint, int, text, text, text, timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION kg.w_projection_set(bigint, int, text, text, text, timestamptz) TO pipeshub_connectors;
SET LOCAL ROLE pipeshub_owner;
REVOKE CREATE ON SCHEMA kg FROM pipeshub_vetted_kg;
REVOKE pipeshub_vetted_kg FROM pipeshub_owner;
SELECT kernel.register_function('kg.w_projection_set(bigint, int, text, text, text, timestamptz)', 'write', 'pipeshub_connectors', 'org', false);
SELECT kernel.allow_vetted_write_op('kg.w_projection_set(bigint, int, text, text, text, timestamptz)', 'kg.projection_state', 'upsert', NULL, ARRAY['code_epoch', 'mode', 'verify_digest', 'backfill_cursor', 'verified_at']::name[], NULL, 5000);
