-- generated from the manifest by python -m db.lib.vetted render: edit the manifest, not this file
GRANT pipeshub_vetted_kg TO pipeshub_owner WITH SET TRUE;
GRANT USAGE ON SCHEMA kg TO pipeshub_vetted_kg;           -- the body names objects of its own schema
GRANT CREATE ON SCHEMA kg TO pipeshub_vetted_kg;
SET LOCAL ROLE pipeshub_vetted_kg;
CREATE FUNCTION kg.w_records_upsert(p_sks bigint[], p_colls smallint[], p_ids text[], p_kinds smallint[], p_app_sks bigint[], p_group_sks bigint[], p_parent_sks bigint[], p_rules text[], p_models text[], p_vrids text[], p_names text[], p_name_keys text[], p_updated bigint[], p_created bigint[], p_sizes bigint[], p_record_types text[], p_origins text[], p_external_ids text[], p_deleted boolean[], p_placeholders boolean[], p_granted boolean[])
RETURNS int LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, kg, pg_temp
SET standard_conforming_strings = on
SET plan_cache_mode = force_custom_plan
AS $fn$
DECLARE v_org text := NULLIF(current_setting('app.org_id', true), '');
BEGIN
  IF p_sks IS NULL THEN RAISE EXCEPTION 'w_records_upsert: p_sks is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_sks) > 5000 THEN RAISE EXCEPTION 'w_records_upsert: p_sks takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_sks) e WHERE e IS NULL) THEN RAISE EXCEPTION 'w_records_upsert: p_sks has a NULL item' USING ERRCODE = '22023'; END IF;
  IF p_colls IS NULL THEN RAISE EXCEPTION 'w_records_upsert: p_colls is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_colls) > 5000 THEN RAISE EXCEPTION 'w_records_upsert: p_colls takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_colls) e WHERE e IS NULL) THEN RAISE EXCEPTION 'w_records_upsert: p_colls has a NULL item' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_colls) e WHERE e IS NOT NULL AND e NOT BETWEEN 1 AND 3) THEN RAISE EXCEPTION 'w_records_upsert: p_colls has an item out of range' USING ERRCODE = '22023'; END IF;
  IF p_ids IS NULL THEN RAISE EXCEPTION 'w_records_upsert: p_ids is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_ids) > 5000 THEN RAISE EXCEPTION 'w_records_upsert: p_ids takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_ids) e WHERE e IS NULL) THEN RAISE EXCEPTION 'w_records_upsert: p_ids has a NULL item' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_ids) e WHERE e IS NOT NULL AND length(e) NOT BETWEEN 1 AND 256) THEN RAISE EXCEPTION 'w_records_upsert: p_ids has an item of the wrong length' USING ERRCODE = '22023'; END IF;
  IF p_kinds IS NULL THEN RAISE EXCEPTION 'w_records_upsert: p_kinds is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_kinds) > 5000 THEN RAISE EXCEPTION 'w_records_upsert: p_kinds takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_kinds) e WHERE e IS NULL) THEN RAISE EXCEPTION 'w_records_upsert: p_kinds has a NULL item' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_kinds) e WHERE e IS NOT NULL AND e NOT BETWEEN 1 AND 3) THEN RAISE EXCEPTION 'w_records_upsert: p_kinds has an item out of range' USING ERRCODE = '22023'; END IF;
  IF p_app_sks IS NULL THEN RAISE EXCEPTION 'w_records_upsert: p_app_sks is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_app_sks) > 5000 THEN RAISE EXCEPTION 'w_records_upsert: p_app_sks takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF p_group_sks IS NULL THEN RAISE EXCEPTION 'w_records_upsert: p_group_sks is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_group_sks) > 5000 THEN RAISE EXCEPTION 'w_records_upsert: p_group_sks takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF p_parent_sks IS NULL THEN RAISE EXCEPTION 'w_records_upsert: p_parent_sks is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_parent_sks) > 5000 THEN RAISE EXCEPTION 'w_records_upsert: p_parent_sks takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF p_rules IS NULL THEN RAISE EXCEPTION 'w_records_upsert: p_rules is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_rules) > 5000 THEN RAISE EXCEPTION 'w_records_upsert: p_rules takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_rules) e WHERE e IS NOT NULL AND e NOT IN ('OPEN', 'STRICT', 'RESTRICTED')) THEN RAISE EXCEPTION 'w_records_upsert: p_rules has an item that is not one of the allowed values' USING ERRCODE = '22023'; END IF;
  IF p_models IS NULL THEN RAISE EXCEPTION 'w_records_upsert: p_models is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_models) > 5000 THEN RAISE EXCEPTION 'w_records_upsert: p_models takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_models) e WHERE e IS NOT NULL AND e NOT IN ('APP_LEVEL', 'RECORD_LEVEL', 'RECORD_GROUP_LEVEL')) THEN RAISE EXCEPTION 'w_records_upsert: p_models has an item that is not one of the allowed values' USING ERRCODE = '22023'; END IF;
  IF p_vrids IS NULL THEN RAISE EXCEPTION 'w_records_upsert: p_vrids is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_vrids) > 5000 THEN RAISE EXCEPTION 'w_records_upsert: p_vrids takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_vrids) e WHERE e IS NOT NULL AND length(e) NOT BETWEEN 0 AND 256) THEN RAISE EXCEPTION 'w_records_upsert: p_vrids has an item of the wrong length' USING ERRCODE = '22023'; END IF;
  IF p_names IS NULL THEN RAISE EXCEPTION 'w_records_upsert: p_names is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_names) > 5000 THEN RAISE EXCEPTION 'w_records_upsert: p_names takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_names) e WHERE e IS NOT NULL AND length(e) NOT BETWEEN 0 AND 4000) THEN RAISE EXCEPTION 'w_records_upsert: p_names has an item of the wrong length' USING ERRCODE = '22023'; END IF;
  IF p_name_keys IS NULL THEN RAISE EXCEPTION 'w_records_upsert: p_name_keys is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_name_keys) > 5000 THEN RAISE EXCEPTION 'w_records_upsert: p_name_keys takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_name_keys) e WHERE e IS NOT NULL AND length(e) NOT BETWEEN 0 AND 4000) THEN RAISE EXCEPTION 'w_records_upsert: p_name_keys has an item of the wrong length' USING ERRCODE = '22023'; END IF;
  IF p_updated IS NULL THEN RAISE EXCEPTION 'w_records_upsert: p_updated is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_updated) > 5000 THEN RAISE EXCEPTION 'w_records_upsert: p_updated takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF p_created IS NULL THEN RAISE EXCEPTION 'w_records_upsert: p_created is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_created) > 5000 THEN RAISE EXCEPTION 'w_records_upsert: p_created takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF p_sizes IS NULL THEN RAISE EXCEPTION 'w_records_upsert: p_sizes is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_sizes) > 5000 THEN RAISE EXCEPTION 'w_records_upsert: p_sizes takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF p_record_types IS NULL THEN RAISE EXCEPTION 'w_records_upsert: p_record_types is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_record_types) > 5000 THEN RAISE EXCEPTION 'w_records_upsert: p_record_types takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_record_types) e WHERE e IS NOT NULL AND length(e) NOT BETWEEN 0 AND 64) THEN RAISE EXCEPTION 'w_records_upsert: p_record_types has an item of the wrong length' USING ERRCODE = '22023'; END IF;
  IF p_origins IS NULL THEN RAISE EXCEPTION 'w_records_upsert: p_origins is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_origins) > 5000 THEN RAISE EXCEPTION 'w_records_upsert: p_origins takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_origins) e WHERE e IS NOT NULL AND e NOT IN ('CONNECTOR', 'UPLOAD')) THEN RAISE EXCEPTION 'w_records_upsert: p_origins has an item that is not one of the allowed values' USING ERRCODE = '22023'; END IF;
  IF p_external_ids IS NULL THEN RAISE EXCEPTION 'w_records_upsert: p_external_ids is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_external_ids) > 5000 THEN RAISE EXCEPTION 'w_records_upsert: p_external_ids takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_external_ids) e WHERE e IS NOT NULL AND length(e) NOT BETWEEN 0 AND 2000) THEN RAISE EXCEPTION 'w_records_upsert: p_external_ids has an item of the wrong length' USING ERRCODE = '22023'; END IF;
  IF p_deleted IS NULL THEN RAISE EXCEPTION 'w_records_upsert: p_deleted is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_deleted) > 5000 THEN RAISE EXCEPTION 'w_records_upsert: p_deleted takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_deleted) e WHERE e IS NULL) THEN RAISE EXCEPTION 'w_records_upsert: p_deleted has a NULL item' USING ERRCODE = '22023'; END IF;
  IF p_placeholders IS NULL THEN RAISE EXCEPTION 'w_records_upsert: p_placeholders is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_placeholders) > 5000 THEN RAISE EXCEPTION 'w_records_upsert: p_placeholders takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_placeholders) e WHERE e IS NULL) THEN RAISE EXCEPTION 'w_records_upsert: p_placeholders has a NULL item' USING ERRCODE = '22023'; END IF;
  IF p_granted IS NULL THEN RAISE EXCEPTION 'w_records_upsert: p_granted is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_granted) > 5000 THEN RAISE EXCEPTION 'w_records_upsert: p_granted takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_granted) e WHERE e IS NULL) THEN RAISE EXCEPTION 'w_records_upsert: p_granted has a NULL item' USING ERRCODE = '22023'; END IF;
  IF v_org IS NULL THEN RETURN 0; END IF;       -- no org (or someone else's): no rows, the same answer
  DECLARE v_n integer;
  BEGIN
    IF cardinality(p_colls) <> cardinality(p_sks) OR cardinality(p_ids) <> cardinality(p_sks) OR cardinality(p_kinds) <> cardinality(p_sks) OR cardinality(p_app_sks) <> cardinality(p_sks) OR cardinality(p_group_sks) <> cardinality(p_sks) OR cardinality(p_parent_sks) <> cardinality(p_sks) OR cardinality(p_rules) <> cardinality(p_sks) OR cardinality(p_models) <> cardinality(p_sks) OR cardinality(p_vrids) <> cardinality(p_sks) OR cardinality(p_names) <> cardinality(p_sks) OR cardinality(p_name_keys) <> cardinality(p_sks) OR cardinality(p_updated) <> cardinality(p_sks) OR cardinality(p_created) <> cardinality(p_sks) OR cardinality(p_sizes) <> cardinality(p_sks) OR cardinality(p_record_types) <> cardinality(p_sks) OR cardinality(p_origins) <> cardinality(p_sks) OR cardinality(p_external_ids) <> cardinality(p_sks) OR cardinality(p_deleted) <> cardinality(p_sks) OR cardinality(p_placeholders) <> cardinality(p_sks) OR cardinality(p_granted) <> cardinality(p_sks) THEN
      RAISE EXCEPTION 'the arrays differ in length' USING ERRCODE = '22023';
    END IF;
    -- Sorted by key (locks are taken in key order) and a key named twice keeps its last item
    SELECT array_agg(s.c1 ORDER BY s.c1), array_agg(s.c2 ORDER BY s.c1), array_agg(s.c3 ORDER BY s.c1), array_agg(s.c4 ORDER BY s.c1), array_agg(s.c5 ORDER BY s.c1), array_agg(s.c6 ORDER BY s.c1), array_agg(s.c7 ORDER BY s.c1), array_agg(s.c8 ORDER BY s.c1), array_agg(s.c9 ORDER BY s.c1), array_agg(s.c10 ORDER BY s.c1), array_agg(s.c11 ORDER BY s.c1), array_agg(s.c12 ORDER BY s.c1), array_agg(s.c13 ORDER BY s.c1), array_agg(s.c14 ORDER BY s.c1), array_agg(s.c15 ORDER BY s.c1), array_agg(s.c16 ORDER BY s.c1), array_agg(s.c17 ORDER BY s.c1), array_agg(s.c18 ORDER BY s.c1), array_agg(s.c19 ORDER BY s.c1), array_agg(s.c20 ORDER BY s.c1), array_agg(s.c21 ORDER BY s.c1) INTO p_sks, p_colls, p_ids, p_kinds, p_app_sks, p_group_sks, p_parent_sks, p_rules, p_models, p_vrids, p_names, p_name_keys, p_updated, p_created, p_sizes, p_record_types, p_origins, p_external_ids, p_deleted, p_placeholders, p_granted FROM (SELECT DISTINCT ON (k.c1) k.c1, k.c2, k.c3, k.c4, k.c5, k.c6, k.c7, k.c8, k.c9, k.c10, k.c11, k.c12, k.c13, k.c14, k.c15, k.c16, k.c17, k.c18, k.c19, k.c20, k.c21 FROM unnest(p_sks, p_colls, p_ids, p_kinds, p_app_sks, p_group_sks, p_parent_sks, p_rules, p_models, p_vrids, p_names, p_name_keys, p_updated, p_created, p_sizes, p_record_types, p_origins, p_external_ids, p_deleted, p_placeholders, p_granted) WITH ORDINALITY AS k(c1, c2, c3, c4, c5, c6, c7, c8, c9, c10, c11, c12, c13, c14, c15, c16, c17, c18, c19, c20, c21, n) ORDER BY k.c1, k.n DESC) s;
    INSERT INTO kg.records (org_id, sk, coll, id, kind, app_sk, group_sk, parent_sk, rule, model, vrid, name, name_key, updated_ms, created_ms, size_bytes, record_type, origin, external_id, deleted, placeholder, granted) SELECT v_org, k.a, k.b, k.c, k.d, k.e, k.f, k.g, k.h, k.i, k.j, k.k, k.l, k.m, k.n, k.o, k.p, k.q, k.r, k.s, k.t, k.u FROM unnest(p_sks, p_colls, p_ids, p_kinds, p_app_sks, p_group_sks, p_parent_sks, p_rules, p_models, p_vrids, p_names, p_name_keys, p_updated, p_created, p_sizes, p_record_types, p_origins, p_external_ids, p_deleted, p_placeholders, p_granted) AS k(a, b, c, d, e, f, g, h, i, j, k, l, m, n, o, p, q, r, s, t, u)
      ON CONFLICT (org_id, sk) DO UPDATE SET coll = excluded.coll, id = excluded.id, kind = excluded.kind, app_sk = excluded.app_sk, group_sk = excluded.group_sk, parent_sk = excluded.parent_sk, rule = excluded.rule, model = excluded.model, vrid = excluded.vrid, name = excluded.name, name_key = excluded.name_key, updated_ms = excluded.updated_ms, created_ms = excluded.created_ms, size_bytes = excluded.size_bytes, record_type = excluded.record_type, origin = excluded.origin, external_id = excluded.external_id, deleted = excluded.deleted, placeholder = excluded.placeholder, granted = excluded.granted;
    GET DIAGNOSTICS v_n = ROW_COUNT;
    RETURN v_n;
  END;
END $fn$;
REVOKE ALL ON FUNCTION kg.w_records_upsert(bigint[], smallint[], text[], smallint[], bigint[], bigint[], bigint[], text[], text[], text[], text[], text[], bigint[], bigint[], bigint[], text[], text[], text[], boolean[], boolean[], boolean[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION kg.w_records_upsert(bigint[], smallint[], text[], smallint[], bigint[], bigint[], bigint[], text[], text[], text[], text[], text[], bigint[], bigint[], bigint[], text[], text[], text[], boolean[], boolean[], boolean[]) TO pipeshub_connectors;
SET LOCAL ROLE pipeshub_owner;
REVOKE CREATE ON SCHEMA kg FROM pipeshub_vetted_kg;
REVOKE pipeshub_vetted_kg FROM pipeshub_owner;
SELECT kernel.register_function('kg.w_records_upsert(bigint[], smallint[], text[], smallint[], bigint[], bigint[], bigint[], text[], text[], text[], text[], text[], bigint[], bigint[], bigint[], text[], text[], text[], boolean[], boolean[], boolean[])', 'write', 'pipeshub_connectors', 'org', false);
SELECT kernel.allow_vetted_write_op('kg.w_records_upsert(bigint[], smallint[], text[], smallint[], bigint[], bigint[], bigint[], text[], text[], text[], text[], text[], bigint[], bigint[], bigint[], text[], text[], text[], boolean[], boolean[], boolean[])', 'kg.records', 'upsert', NULL, ARRAY['coll', 'id', 'kind', 'app_sk', 'group_sk', 'parent_sk', 'rule', 'model', 'vrid', 'name', 'name_key', 'updated_ms', 'created_ms', 'size_bytes', 'record_type', 'origin', 'external_id', 'deleted', 'placeholder', 'granted']::name[], NULL, 5000);
