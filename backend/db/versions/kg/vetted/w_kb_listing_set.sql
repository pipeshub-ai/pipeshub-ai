-- generated from the manifest by python -m db.lib.vetted render: edit the manifest, not this file
GRANT pipeshub_vetted_kg TO pipeshub_owner WITH SET TRUE;
GRANT USAGE ON SCHEMA kg TO pipeshub_vetted_kg;           -- the body names objects of its own schema
GRANT CREATE ON SCHEMA kg TO pipeshub_vetted_kg;
SET LOCAL ROLE pipeshub_vetted_kg;
CREATE FUNCTION kg.w_kb_listing_set(p_sks bigint[], p_colls smallint[], p_ids text[], p_kinds smallint[], p_statuses text[], p_connector_names text[], p_created_ms bigint[], p_updated_ms bigint[], p_file_sizes bigint[], p_file_kinds smallint[], p_odd boolean[])
RETURNS int LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, kg, pg_temp
SET standard_conforming_strings = on
SET plan_cache_mode = force_custom_plan
AS $fn$
DECLARE v_org text := NULLIF(current_setting('app.org_id', true), '');
BEGIN
  IF p_sks IS NULL THEN RAISE EXCEPTION 'w_kb_listing_set: p_sks is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_sks) > 5000 THEN RAISE EXCEPTION 'w_kb_listing_set: p_sks takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_sks) e WHERE e IS NULL) THEN RAISE EXCEPTION 'w_kb_listing_set: p_sks has a NULL item' USING ERRCODE = '22023'; END IF;
  IF p_colls IS NULL THEN RAISE EXCEPTION 'w_kb_listing_set: p_colls is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_colls) > 5000 THEN RAISE EXCEPTION 'w_kb_listing_set: p_colls takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_colls) e WHERE e IS NULL) THEN RAISE EXCEPTION 'w_kb_listing_set: p_colls has a NULL item' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_colls) e WHERE e IS NOT NULL AND e NOT BETWEEN 1 AND 3) THEN RAISE EXCEPTION 'w_kb_listing_set: p_colls has an item out of range' USING ERRCODE = '22023'; END IF;
  IF p_ids IS NULL THEN RAISE EXCEPTION 'w_kb_listing_set: p_ids is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_ids) > 5000 THEN RAISE EXCEPTION 'w_kb_listing_set: p_ids takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_ids) e WHERE e IS NULL) THEN RAISE EXCEPTION 'w_kb_listing_set: p_ids has a NULL item' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_ids) e WHERE e IS NOT NULL AND length(e) NOT BETWEEN 1 AND 256) THEN RAISE EXCEPTION 'w_kb_listing_set: p_ids has an item of the wrong length' USING ERRCODE = '22023'; END IF;
  IF p_kinds IS NULL THEN RAISE EXCEPTION 'w_kb_listing_set: p_kinds is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_kinds) > 5000 THEN RAISE EXCEPTION 'w_kb_listing_set: p_kinds takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_kinds) e WHERE e IS NULL) THEN RAISE EXCEPTION 'w_kb_listing_set: p_kinds has a NULL item' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_kinds) e WHERE e IS NOT NULL AND e NOT BETWEEN 1 AND 3) THEN RAISE EXCEPTION 'w_kb_listing_set: p_kinds has an item out of range' USING ERRCODE = '22023'; END IF;
  IF p_statuses IS NULL THEN RAISE EXCEPTION 'w_kb_listing_set: p_statuses is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_statuses) > 5000 THEN RAISE EXCEPTION 'w_kb_listing_set: p_statuses takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_statuses) e WHERE e IS NOT NULL AND length(e) NOT BETWEEN 0 AND 256) THEN RAISE EXCEPTION 'w_kb_listing_set: p_statuses has an item of the wrong length' USING ERRCODE = '22023'; END IF;
  IF p_connector_names IS NULL THEN RAISE EXCEPTION 'w_kb_listing_set: p_connector_names is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_connector_names) > 5000 THEN RAISE EXCEPTION 'w_kb_listing_set: p_connector_names takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_connector_names) e WHERE e IS NOT NULL AND length(e) NOT BETWEEN 0 AND 256) THEN RAISE EXCEPTION 'w_kb_listing_set: p_connector_names has an item of the wrong length' USING ERRCODE = '22023'; END IF;
  IF p_created_ms IS NULL THEN RAISE EXCEPTION 'w_kb_listing_set: p_created_ms is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_created_ms) > 5000 THEN RAISE EXCEPTION 'w_kb_listing_set: p_created_ms takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF p_updated_ms IS NULL THEN RAISE EXCEPTION 'w_kb_listing_set: p_updated_ms is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_updated_ms) > 5000 THEN RAISE EXCEPTION 'w_kb_listing_set: p_updated_ms takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF p_file_sizes IS NULL THEN RAISE EXCEPTION 'w_kb_listing_set: p_file_sizes is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_file_sizes) > 5000 THEN RAISE EXCEPTION 'w_kb_listing_set: p_file_sizes takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF p_file_kinds IS NULL THEN RAISE EXCEPTION 'w_kb_listing_set: p_file_kinds is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_file_kinds) > 5000 THEN RAISE EXCEPTION 'w_kb_listing_set: p_file_kinds takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_file_kinds) e WHERE e IS NOT NULL AND e NOT BETWEEN 0 AND 3) THEN RAISE EXCEPTION 'w_kb_listing_set: p_file_kinds has an item out of range' USING ERRCODE = '22023'; END IF;
  IF p_odd IS NULL THEN RAISE EXCEPTION 'w_kb_listing_set: p_odd is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_odd) > 5000 THEN RAISE EXCEPTION 'w_kb_listing_set: p_odd takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_odd) e WHERE e IS NULL) THEN RAISE EXCEPTION 'w_kb_listing_set: p_odd has a NULL item' USING ERRCODE = '22023'; END IF;
  IF v_org IS NULL THEN RETURN 0; END IF;       -- no org (or someone else's): no rows, the same answer
  DECLARE v_n integer;
  BEGIN
    IF cardinality(p_colls) <> cardinality(p_sks) OR cardinality(p_ids) <> cardinality(p_sks) OR cardinality(p_kinds) <> cardinality(p_sks) OR cardinality(p_statuses) <> cardinality(p_sks)
       OR cardinality(p_connector_names) <> cardinality(p_sks) OR cardinality(p_created_ms) <> cardinality(p_sks) OR cardinality(p_updated_ms) <> cardinality(p_sks)
       OR cardinality(p_file_sizes) <> cardinality(p_sks) OR cardinality(p_file_kinds) <> cardinality(p_sks) OR cardinality(p_odd) <> cardinality(p_sks) THEN
      RAISE EXCEPTION 'the arrays differ in length' USING ERRCODE = '22023';
    END IF;
    INSERT INTO kg.records (org_id, sk, coll, id, kind, indexing_status, connector_name, doc_created_ms, doc_updated_ms, file_size, file_kind, kb_odd)
      SELECT v_org, k.a, k.b, k.c, k.d, k.e, k.f, k.g, k.h, k.i, k.j, k.k
      FROM unnest(p_sks, p_colls, p_ids, p_kinds, p_statuses, p_connector_names, p_created_ms, p_updated_ms, p_file_sizes, p_file_kinds, p_odd) AS k(a, b, c, d, e, f, g, h, i, j, k)
      ON CONFLICT (org_id, sk) DO UPDATE SET indexing_status = excluded.indexing_status, connector_name = excluded.connector_name, doc_created_ms = excluded.doc_created_ms,
        doc_updated_ms = excluded.doc_updated_ms, file_size = excluded.file_size, file_kind = excluded.file_kind, kb_odd = excluded.kb_odd;
    GET DIAGNOSTICS v_n = ROW_COUNT;
    RETURN v_n;
  END;
END $fn$;
REVOKE ALL ON FUNCTION kg.w_kb_listing_set(bigint[], smallint[], text[], smallint[], text[], text[], bigint[], bigint[], bigint[], smallint[], boolean[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION kg.w_kb_listing_set(bigint[], smallint[], text[], smallint[], text[], text[], bigint[], bigint[], bigint[], smallint[], boolean[]) TO pipeshub_connectors;
SET LOCAL ROLE pipeshub_owner;
REVOKE CREATE ON SCHEMA kg FROM pipeshub_vetted_kg;
REVOKE pipeshub_vetted_kg FROM pipeshub_owner;
SELECT kernel.register_function('kg.w_kb_listing_set(bigint[], smallint[], text[], smallint[], text[], text[], bigint[], bigint[], bigint[], smallint[], boolean[])', 'write', 'pipeshub_connectors', 'org', false);
SELECT kernel.allow_vetted_write_op('kg.w_kb_listing_set(bigint[], smallint[], text[], smallint[], text[], text[], bigint[], bigint[], bigint[], smallint[], boolean[])', 'kg.records', 'upsert', NULL, ARRAY['indexing_status', 'connector_name', 'doc_created_ms', 'doc_updated_ms', 'file_size', 'file_kind', 'kb_odd']::name[], NULL, 5000);
