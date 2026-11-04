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
    IF EXISTS (SELECT 1 FROM unnest(p_clauses) AS e WHERE jsonb_typeof(e) <> 'array') THEN
      RAISE EXCEPTION 'w_classes_set: the clauses of a class are an array' USING ERRCODE = '22023';
    END IF;
    -- A class is content-addressed: an id that is named twice, or already stored, with other content is refused, never silently kept as it was.
    IF EXISTS (SELECT 1 FROM unnest(p_ids, p_app_sks, p_group_sks, p_clauses, p_hashes) AS k(a, b, c, d, e) GROUP BY k.a HAVING count(DISTINCT (k.b, k.c, k.d, k.e)) > 1)
       OR EXISTS (SELECT 1 FROM kg.scope_classes t JOIN unnest(p_ids, p_app_sks, p_group_sks, p_clauses, p_hashes) AS k(a, b, c, d, e) ON t.id = k.a
                  WHERE t.org_id = v_org AND (t.app_sk <> k.b OR t.group_sk IS DISTINCT FROM k.c OR t.clauses <> k.d OR t.hash <> k.e)) THEN
      RAISE EXCEPTION 'w_classes_set: the class id is taken by other content' USING ERRCODE = '22023';
    END IF;
    -- Sorted by key (locks are taken in key order) and a key named twice keeps its last item
    SELECT array_agg(s.c1 ORDER BY s.c1), array_agg(s.c2 ORDER BY s.c1), array_agg(s.c3 ORDER BY s.c1), array_agg(s.c4 ORDER BY s.c1), array_agg(s.c5 ORDER BY s.c1) INTO p_ids, p_app_sks, p_group_sks, p_clauses, p_hashes FROM (SELECT DISTINCT ON (k.c1) k.c1, k.c2, k.c3, k.c4, k.c5 FROM unnest(p_ids, p_app_sks, p_group_sks, p_clauses, p_hashes) WITH ORDINALITY AS k(c1, c2, c3, c4, c5, n) ORDER BY k.c1, k.n DESC) s;
    INSERT INTO kg.scope_classes (org_id, id, app_sk, group_sk, clauses, hash) SELECT v_org, k.a, k.b, k.c, k.d, k.e FROM unnest(p_ids, p_app_sks, p_group_sks, p_clauses, p_hashes) AS k(a, b, c, d, e)
      ON CONFLICT (org_id, id) DO NOTHING;
    GET DIAGNOSTICS v_n = ROW_COUNT;
    -- A row a concurrent writer committed between the check above and the insert was skipped, not written: whatever is stored under each id is now read again,
    -- and an id that holds other content is refused as it was above (each statement sees what has been committed since the last).
    IF EXISTS (SELECT 1 FROM kg.scope_classes t JOIN unnest(p_ids, p_app_sks, p_group_sks, p_clauses, p_hashes) AS k(a, b, c, d, e) ON t.id = k.a
               WHERE t.org_id = v_org AND (t.app_sk <> k.b OR t.group_sk IS DISTINCT FROM k.c OR t.clauses <> k.d OR t.hash <> k.e)) THEN
      RAISE EXCEPTION 'w_classes_set: the class id is taken by other content' USING ERRCODE = '22023';
    END IF;
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
