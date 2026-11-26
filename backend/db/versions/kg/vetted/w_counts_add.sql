-- generated from the manifest by python -m db.lib.vetted render: edit the manifest, not this file
GRANT pipeshub_vetted_kg TO pipeshub_owner WITH SET TRUE;
GRANT USAGE ON SCHEMA kg TO pipeshub_vetted_kg;           -- the body names objects of its own schema
GRANT CREATE ON SCHEMA kg TO pipeshub_vetted_kg;
SET LOCAL ROLE pipeshub_vetted_kg;
CREATE FUNCTION kg.w_counts_add(p_apps bigint[], p_facets smallint[], p_classes bigint[], p_kinds smallint[], p_folders boolean[], p_record_types text[], p_origins text[], p_ns bigint[])
RETURNS int LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, kg, pg_temp
SET standard_conforming_strings = on
SET plan_cache_mode = force_custom_plan
AS $fn$
DECLARE v_org text := NULLIF(current_setting('app.org_id', true), '');
BEGIN
  IF p_apps IS NULL THEN RAISE EXCEPTION 'w_counts_add: p_apps is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_apps) > 5000 THEN RAISE EXCEPTION 'w_counts_add: p_apps takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_apps) e WHERE e IS NULL) THEN RAISE EXCEPTION 'w_counts_add: p_apps has a NULL item' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_apps) e WHERE e IS NOT NULL AND e NOT BETWEEN 0 AND 9223372036854775807) THEN RAISE EXCEPTION 'w_counts_add: p_apps has an item out of range' USING ERRCODE = '22023'; END IF;
  IF p_facets IS NULL THEN RAISE EXCEPTION 'w_counts_add: p_facets is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_facets) > 5000 THEN RAISE EXCEPTION 'w_counts_add: p_facets takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_facets) e WHERE e IS NULL) THEN RAISE EXCEPTION 'w_counts_add: p_facets has a NULL item' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_facets) e WHERE e IS NOT NULL AND e NOT BETWEEN 0 AND 2) THEN RAISE EXCEPTION 'w_counts_add: p_facets has an item out of range' USING ERRCODE = '22023'; END IF;
  IF p_classes IS NULL THEN RAISE EXCEPTION 'w_counts_add: p_classes is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_classes) > 5000 THEN RAISE EXCEPTION 'w_counts_add: p_classes takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF p_kinds IS NULL THEN RAISE EXCEPTION 'w_counts_add: p_kinds is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_kinds) > 5000 THEN RAISE EXCEPTION 'w_counts_add: p_kinds takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_kinds) e WHERE e IS NOT NULL AND e NOT BETWEEN 2 AND 3) THEN RAISE EXCEPTION 'w_counts_add: p_kinds has an item out of range' USING ERRCODE = '22023'; END IF;
  IF p_folders IS NULL THEN RAISE EXCEPTION 'w_counts_add: p_folders is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_folders) > 5000 THEN RAISE EXCEPTION 'w_counts_add: p_folders takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF p_record_types IS NULL THEN RAISE EXCEPTION 'w_counts_add: p_record_types is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_record_types) > 5000 THEN RAISE EXCEPTION 'w_counts_add: p_record_types takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_record_types) e WHERE e IS NOT NULL AND length(e) NOT BETWEEN 0 AND 64) THEN RAISE EXCEPTION 'w_counts_add: p_record_types has an item of the wrong length' USING ERRCODE = '22023'; END IF;
  IF p_origins IS NULL THEN RAISE EXCEPTION 'w_counts_add: p_origins is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_origins) > 5000 THEN RAISE EXCEPTION 'w_counts_add: p_origins takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_origins) e WHERE e IS NOT NULL AND e NOT IN ('CONNECTOR', 'UPLOAD')) THEN RAISE EXCEPTION 'w_counts_add: p_origins has an item that is not one of the allowed values' USING ERRCODE = '22023'; END IF;
  IF p_ns IS NULL THEN RAISE EXCEPTION 'w_counts_add: p_ns is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_ns) > 5000 THEN RAISE EXCEPTION 'w_counts_add: p_ns takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_ns) e WHERE e IS NULL) THEN RAISE EXCEPTION 'w_counts_add: p_ns has a NULL item' USING ERRCODE = '22023'; END IF;
  IF v_org IS NULL THEN RETURN 0; END IF;       -- no org (or someone else's): no rows, the same answer
  DECLARE v_n integer;
  BEGIN
    IF cardinality(p_facets) <> cardinality(p_apps) OR cardinality(p_classes) <> cardinality(p_apps) OR cardinality(p_kinds) <> cardinality(p_apps) OR cardinality(p_folders) <> cardinality(p_apps) OR cardinality(p_record_types) <> cardinality(p_apps) OR cardinality(p_origins) <> cardinality(p_apps) OR cardinality(p_ns) <> cardinality(p_apps) THEN
      RAISE EXCEPTION 'the arrays differ in length' USING ERRCODE = '22023';
    END IF;
    INSERT INTO kg.counts (org_id, app_sk, facet, class_id, kind, folder, record_type, origin, n, folded) SELECT v_org, k.a, k.b, k.c, k.d, k.e, k.f, k.g, k.h, true FROM unnest(p_apps, p_facets, p_classes, p_kinds, p_folders, p_record_types, p_origins, p_ns) AS k(a, b, c, d, e, f, g, h);
    GET DIAGNOSTICS v_n = ROW_COUNT;
    RETURN v_n;
  END;
END $fn$;
REVOKE ALL ON FUNCTION kg.w_counts_add(bigint[], smallint[], bigint[], smallint[], boolean[], text[], text[], bigint[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION kg.w_counts_add(bigint[], smallint[], bigint[], smallint[], boolean[], text[], text[], bigint[]) TO pipeshub_connectors;
SET LOCAL ROLE pipeshub_owner;
REVOKE CREATE ON SCHEMA kg FROM pipeshub_vetted_kg;
REVOKE pipeshub_vetted_kg FROM pipeshub_owner;
SELECT kernel.register_function('kg.w_counts_add(bigint[], smallint[], bigint[], smallint[], boolean[], text[], text[], bigint[])', 'write', 'pipeshub_connectors', 'org', false);
SELECT kernel.allow_vetted_write_op('kg.w_counts_add(bigint[], smallint[], bigint[], smallint[], boolean[], text[], text[], bigint[])', 'kg.counts', 'insert', NULL, NULL, NULL, 5000);
