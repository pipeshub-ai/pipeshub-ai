-- generated from the manifest by python -m db.lib.vetted render: edit the manifest, not this file
GRANT pipeshub_vetted_kg TO pipeshub_owner WITH SET TRUE;
GRANT USAGE ON SCHEMA kg TO pipeshub_vetted_kg;           -- the body names objects of its own schema
GRANT CREATE ON SCHEMA kg TO pipeshub_vetted_kg;
SET LOCAL ROLE pipeshub_vetted_kg;
CREATE FUNCTION kg.w_app_facts_set(p_app_sk bigint, p_opens boolean, p_group_level boolean)
RETURNS boolean LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, kg, pg_temp
SET standard_conforming_strings = on
SET plan_cache_mode = force_custom_plan
AS $fn$
DECLARE v_org text := NULLIF(current_setting('app.org_id', true), '');
BEGIN
  IF p_app_sk IS NULL THEN RAISE EXCEPTION 'w_app_facts_set: p_app_sk is required' USING ERRCODE = '22023'; END IF;
  IF p_app_sk NOT BETWEEN 0 AND 9223372036854775807 THEN RAISE EXCEPTION 'w_app_facts_set: p_app_sk must be 0..9223372036854775807' USING ERRCODE = '22023'; END IF;
  IF p_opens IS NULL THEN RAISE EXCEPTION 'w_app_facts_set: p_opens is required' USING ERRCODE = '22023'; END IF;
  IF p_group_level IS NULL THEN RAISE EXCEPTION 'w_app_facts_set: p_group_level is required' USING ERRCODE = '22023'; END IF;
  IF v_org IS NULL THEN RETURN false; END IF;       -- no org (or someone else's): no rows, the same answer
  INSERT INTO kg.projection_state (org_id, app_sk, opens, has_group_level, facts_set) VALUES (v_org, p_app_sk, p_opens, p_group_level, true)
      ON CONFLICT (org_id, app_sk) DO UPDATE SET opens = excluded.opens, has_group_level = excluded.has_group_level, facts_set = excluded.facts_set;
    RETURN true;
END $fn$;
REVOKE ALL ON FUNCTION kg.w_app_facts_set(bigint, boolean, boolean) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION kg.w_app_facts_set(bigint, boolean, boolean) TO pipeshub_connectors;
SET LOCAL ROLE pipeshub_owner;
REVOKE CREATE ON SCHEMA kg FROM pipeshub_vetted_kg;
REVOKE pipeshub_vetted_kg FROM pipeshub_owner;
SELECT kernel.register_function('kg.w_app_facts_set(bigint, boolean, boolean)', 'write', 'pipeshub_connectors', 'org', false);
SELECT kernel.allow_vetted_write_op('kg.w_app_facts_set(bigint, boolean, boolean)', 'kg.projection_state', 'upsert', NULL, ARRAY['opens', 'has_group_level', 'facts_set']::name[], NULL, 5000);
