-- generated from the manifest by python -m db.lib.vetted render: edit the manifest, not this file
GRANT pipeshub_vetted_kg TO pipeshub_owner WITH SET TRUE;
GRANT USAGE ON SCHEMA kg TO pipeshub_vetted_kg;           -- the body names objects of its own schema
GRANT CREATE ON SCHEMA kg TO pipeshub_vetted_kg;
SET LOCAL ROLE pipeshub_vetted_kg;
CREATE FUNCTION kg.w_fence_rows(p_sks bigint[], p_colls smallint[], p_ids text[], p_kinds smallint[], p_jobs bigint[])
RETURNS int LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, kg, pg_temp
SET standard_conforming_strings = on
SET plan_cache_mode = force_custom_plan
AS $fn$
DECLARE v_org text := NULLIF(current_setting('app.org_id', true), '');
BEGIN
  IF p_sks IS NULL THEN RAISE EXCEPTION 'w_fence_rows: p_sks is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_sks) > 5000 THEN RAISE EXCEPTION 'w_fence_rows: p_sks takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_sks) e WHERE e IS NULL) THEN RAISE EXCEPTION 'w_fence_rows: p_sks has a NULL item' USING ERRCODE = '22023'; END IF;
  IF p_colls IS NULL THEN RAISE EXCEPTION 'w_fence_rows: p_colls is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_colls) > 5000 THEN RAISE EXCEPTION 'w_fence_rows: p_colls takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_colls) e WHERE e IS NULL) THEN RAISE EXCEPTION 'w_fence_rows: p_colls has a NULL item' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_colls) e WHERE e IS NOT NULL AND e NOT BETWEEN 1 AND 3) THEN RAISE EXCEPTION 'w_fence_rows: p_colls has an item out of range' USING ERRCODE = '22023'; END IF;
  IF p_ids IS NULL THEN RAISE EXCEPTION 'w_fence_rows: p_ids is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_ids) > 5000 THEN RAISE EXCEPTION 'w_fence_rows: p_ids takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_ids) e WHERE e IS NULL) THEN RAISE EXCEPTION 'w_fence_rows: p_ids has a NULL item' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_ids) e WHERE e IS NOT NULL AND length(e) NOT BETWEEN 1 AND 256) THEN RAISE EXCEPTION 'w_fence_rows: p_ids has an item of the wrong length' USING ERRCODE = '22023'; END IF;
  IF p_kinds IS NULL THEN RAISE EXCEPTION 'w_fence_rows: p_kinds is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_kinds) > 5000 THEN RAISE EXCEPTION 'w_fence_rows: p_kinds takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_kinds) e WHERE e IS NULL) THEN RAISE EXCEPTION 'w_fence_rows: p_kinds has a NULL item' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_kinds) e WHERE e IS NOT NULL AND e NOT BETWEEN 1 AND 3) THEN RAISE EXCEPTION 'w_fence_rows: p_kinds has an item out of range' USING ERRCODE = '22023'; END IF;
  IF p_jobs IS NULL THEN RAISE EXCEPTION 'w_fence_rows: p_jobs is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_jobs) > 5000 THEN RAISE EXCEPTION 'w_fence_rows: p_jobs takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF v_org IS NULL THEN RETURN 0; END IF;       -- no org (or someone else's): no rows, the same answer
  DECLARE v_n integer;
  BEGIN
    IF cardinality(p_colls) <> cardinality(p_sks) OR cardinality(p_ids) <> cardinality(p_sks) OR cardinality(p_kinds) <> cardinality(p_sks) OR cardinality(p_jobs) <> cardinality(p_sks) THEN
      RAISE EXCEPTION 'the arrays differ in length' USING ERRCODE = '22023';
    END IF;
    INSERT INTO kg.records (org_id, sk, coll, id, kind, fence_job) SELECT v_org, k.a, k.b, k.c, k.d, k.e FROM unnest(p_sks, p_colls, p_ids, p_kinds, p_jobs) AS k(a, b, c, d, e)
      ON CONFLICT (org_id, sk) DO UPDATE SET fence_job = excluded.fence_job;
    GET DIAGNOSTICS v_n = ROW_COUNT;
    RETURN v_n;
  END;
END $fn$;
REVOKE ALL ON FUNCTION kg.w_fence_rows(bigint[], smallint[], text[], smallint[], bigint[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION kg.w_fence_rows(bigint[], smallint[], text[], smallint[], bigint[]) TO pipeshub_connectors;
SET LOCAL ROLE pipeshub_owner;
REVOKE CREATE ON SCHEMA kg FROM pipeshub_vetted_kg;
REVOKE pipeshub_vetted_kg FROM pipeshub_owner;
SELECT kernel.register_function('kg.w_fence_rows(bigint[], smallint[], text[], smallint[], bigint[])', 'write', 'pipeshub_connectors', 'org', false);
SELECT kernel.allow_vetted_write_op('kg.w_fence_rows(bigint[], smallint[], text[], smallint[], bigint[])', 'kg.records', 'upsert', NULL, ARRAY['fence_job']::name[], NULL, 5000);
