-- generated from the manifest by python -m db.lib.vetted render: edit the manifest, not this file
GRANT pipeshub_vetted_kg TO pipeshub_owner WITH SET TRUE;
GRANT USAGE ON SCHEMA kg TO pipeshub_vetted_kg;           -- the body names objects of its own schema
GRANT CREATE ON SCHEMA kg TO pipeshub_vetted_kg;
SET LOCAL ROLE pipeshub_vetted_kg;
CREATE FUNCTION kg.w_classes_set(p_ids bigint[], p_app_sks bigint[], p_group_sks bigint[], p_clauses jsonb[], p_hashes bytea[])
RETURNS int LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, kg, pg_temp
SET standard_conforming_strings = on
SET plan_cache_mode = force_custom_plan
AS $fn$
DECLARE v_org text := NULLIF(current_setting('app.org_id', true), '');
BEGIN
  IF p_ids IS NULL THEN RAISE EXCEPTION 'w_classes_set: p_ids is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_ids) > 5000 THEN RAISE EXCEPTION 'w_classes_set: p_ids takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_ids) e WHERE e IS NULL) THEN RAISE EXCEPTION 'w_classes_set: p_ids has a NULL item' USING ERRCODE = '22023'; END IF;
  IF p_app_sks IS NULL THEN RAISE EXCEPTION 'w_classes_set: p_app_sks is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_app_sks) > 5000 THEN RAISE EXCEPTION 'w_classes_set: p_app_sks takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_app_sks) e WHERE e IS NULL) THEN RAISE EXCEPTION 'w_classes_set: p_app_sks has a NULL item' USING ERRCODE = '22023'; END IF;
  IF p_group_sks IS NULL THEN RAISE EXCEPTION 'w_classes_set: p_group_sks is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_group_sks) > 5000 THEN RAISE EXCEPTION 'w_classes_set: p_group_sks takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF p_clauses IS NULL THEN RAISE EXCEPTION 'w_classes_set: p_clauses is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_clauses) > 5000 THEN RAISE EXCEPTION 'w_classes_set: p_clauses takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_clauses) e WHERE e IS NULL) THEN RAISE EXCEPTION 'w_classes_set: p_clauses has a NULL item' USING ERRCODE = '22023'; END IF;
  IF p_hashes IS NULL THEN RAISE EXCEPTION 'w_classes_set: p_hashes is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_hashes) > 5000 THEN RAISE EXCEPTION 'w_classes_set: p_hashes takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_hashes) e WHERE e IS NULL) THEN RAISE EXCEPTION 'w_classes_set: p_hashes has a NULL item' USING ERRCODE = '22023'; END IF;
  IF v_org IS NULL THEN RETURN 0; END IF;       -- no org (or someone else's): no rows, the same answer
  DECLARE v_n integer;
  BEGIN
    IF cardinality(p_app_sks) <> cardinality(p_ids) OR cardinality(p_group_sks) <> cardinality(p_ids) OR cardinality(p_clauses) <> cardinality(p_ids) OR cardinality(p_hashes) <> cardinality(p_ids) THEN
      RAISE EXCEPTION 'the arrays differ in length' USING ERRCODE = '22023';
    END IF;
    INSERT INTO kg.scope_classes (org_id, id, app_sk, group_sk, clauses, hash) SELECT v_org, k.a, k.b, k.c, k.d, k.e FROM unnest(p_ids, p_app_sks, p_group_sks, p_clauses, p_hashes) AS k(a, b, c, d, e)
      ON CONFLICT (org_id, id) DO NOTHING;
    GET DIAGNOSTICS v_n = ROW_COUNT;
    RETURN v_n;
  END;
END $fn$;
REVOKE ALL ON FUNCTION kg.w_classes_set(bigint[], bigint[], bigint[], jsonb[], bytea[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION kg.w_classes_set(bigint[], bigint[], bigint[], jsonb[], bytea[]) TO pipeshub_connectors;
SET LOCAL ROLE pipeshub_owner;
REVOKE CREATE ON SCHEMA kg FROM pipeshub_vetted_kg;
REVOKE pipeshub_vetted_kg FROM pipeshub_owner;
SELECT kernel.register_function('kg.w_classes_set(bigint[], bigint[], bigint[], jsonb[], bytea[])', 'write', 'pipeshub_connectors', 'org', false);
SELECT kernel.allow_vetted_write_op('kg.w_classes_set(bigint[], bigint[], bigint[], jsonb[], bytea[])', 'kg.scope_classes', 'upsert', NULL, ARRAY[]::name[], NULL, 5000);
