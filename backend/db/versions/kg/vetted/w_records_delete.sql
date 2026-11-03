-- generated from the manifest by python -m db.lib.vetted render: edit the manifest, not this file
GRANT pipeshub_vetted_kg TO pipeshub_owner WITH SET TRUE;
GRANT USAGE ON SCHEMA kg TO pipeshub_vetted_kg;           -- the body names objects of its own schema
GRANT CREATE ON SCHEMA kg TO pipeshub_vetted_kg;
SET LOCAL ROLE pipeshub_vetted_kg;
CREATE FUNCTION kg.w_records_delete(p_sks bigint[])
RETURNS int LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, kg, pg_temp
SET standard_conforming_strings = on
SET plan_cache_mode = force_custom_plan
AS $fn$
DECLARE v_org text := NULLIF(current_setting('app.org_id', true), '');
BEGIN
  IF p_sks IS NULL THEN RAISE EXCEPTION 'w_records_delete: p_sks is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_sks) > 5000 THEN RAISE EXCEPTION 'w_records_delete: p_sks takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_sks) e WHERE e IS NULL) THEN RAISE EXCEPTION 'w_records_delete: p_sks has a NULL item' USING ERRCODE = '22023'; END IF;
  IF v_org IS NULL THEN RETURN 0; END IF;       -- no org (or someone else's): no rows, the same answer
  DECLARE v_n integer;
  BEGIN
    DELETE FROM kg.records t USING unnest(p_sks) AS k(a) WHERE t.org_id = v_org AND t.sk = k.a;
    GET DIAGNOSTICS v_n = ROW_COUNT;
    RETURN v_n;
  END;
END $fn$;
REVOKE ALL ON FUNCTION kg.w_records_delete(bigint[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION kg.w_records_delete(bigint[]) TO pipeshub_connectors;
SET LOCAL ROLE pipeshub_owner;
REVOKE CREATE ON SCHEMA kg FROM pipeshub_vetted_kg;
REVOKE pipeshub_vetted_kg FROM pipeshub_owner;
SELECT kernel.register_function('kg.w_records_delete(bigint[])', 'write', 'pipeshub_connectors', 'org', false);
SELECT kernel.allow_vetted_write_op('kg.w_records_delete(bigint[])', 'kg.records', 'delete', ARRAY['sk']::name[], NULL, NULL, 5000);
