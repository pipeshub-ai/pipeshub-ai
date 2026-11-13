-- generated from the manifest by python -m db.lib.vetted render: edit the manifest, not this file
GRANT pipeshub_vetted_kg TO pipeshub_owner WITH SET TRUE;
GRANT USAGE ON SCHEMA kg TO pipeshub_vetted_kg;           -- the body names objects of its own schema
GRANT CREATE ON SCHEMA kg TO pipeshub_vetted_kg;
SET LOCAL ROLE pipeshub_vetted_kg;
CREATE FUNCTION kg.w_listing_set(p_sks bigint[], p_colls smallint[], p_ids text[], p_kinds smallint[], p_folders boolean[], p_hide_children boolean[], p_aliens boolean[], p_name_sorts bytea[])
RETURNS int LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, kg, pg_temp
SET standard_conforming_strings = on
SET plan_cache_mode = force_custom_plan
AS $fn$
DECLARE v_org text := NULLIF(current_setting('app.org_id', true), '');
BEGIN
  IF p_sks IS NULL THEN RAISE EXCEPTION 'w_listing_set: p_sks is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_sks) > 5000 THEN RAISE EXCEPTION 'w_listing_set: p_sks takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_sks) e WHERE e IS NULL) THEN RAISE EXCEPTION 'w_listing_set: p_sks has a NULL item' USING ERRCODE = '22023'; END IF;
  IF p_colls IS NULL THEN RAISE EXCEPTION 'w_listing_set: p_colls is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_colls) > 5000 THEN RAISE EXCEPTION 'w_listing_set: p_colls takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_colls) e WHERE e IS NULL) THEN RAISE EXCEPTION 'w_listing_set: p_colls has a NULL item' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_colls) e WHERE e IS NOT NULL AND e NOT BETWEEN 1 AND 3) THEN RAISE EXCEPTION 'w_listing_set: p_colls has an item out of range' USING ERRCODE = '22023'; END IF;
  IF p_ids IS NULL THEN RAISE EXCEPTION 'w_listing_set: p_ids is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_ids) > 5000 THEN RAISE EXCEPTION 'w_listing_set: p_ids takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_ids) e WHERE e IS NULL) THEN RAISE EXCEPTION 'w_listing_set: p_ids has a NULL item' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_ids) e WHERE e IS NOT NULL AND length(e) NOT BETWEEN 1 AND 256) THEN RAISE EXCEPTION 'w_listing_set: p_ids has an item of the wrong length' USING ERRCODE = '22023'; END IF;
  IF p_kinds IS NULL THEN RAISE EXCEPTION 'w_listing_set: p_kinds is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_kinds) > 5000 THEN RAISE EXCEPTION 'w_listing_set: p_kinds takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_kinds) e WHERE e IS NULL) THEN RAISE EXCEPTION 'w_listing_set: p_kinds has a NULL item' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_kinds) e WHERE e IS NOT NULL AND e NOT BETWEEN 1 AND 3) THEN RAISE EXCEPTION 'w_listing_set: p_kinds has an item out of range' USING ERRCODE = '22023'; END IF;
  IF p_folders IS NULL THEN RAISE EXCEPTION 'w_listing_set: p_folders is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_folders) > 5000 THEN RAISE EXCEPTION 'w_listing_set: p_folders takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_folders) e WHERE e IS NULL) THEN RAISE EXCEPTION 'w_listing_set: p_folders has a NULL item' USING ERRCODE = '22023'; END IF;
  IF p_hide_children IS NULL THEN RAISE EXCEPTION 'w_listing_set: p_hide_children is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_hide_children) > 5000 THEN RAISE EXCEPTION 'w_listing_set: p_hide_children takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_hide_children) e WHERE e IS NULL) THEN RAISE EXCEPTION 'w_listing_set: p_hide_children has a NULL item' USING ERRCODE = '22023'; END IF;
  IF p_aliens IS NULL THEN RAISE EXCEPTION 'w_listing_set: p_aliens is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_aliens) > 5000 THEN RAISE EXCEPTION 'w_listing_set: p_aliens takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_aliens) e WHERE e IS NULL) THEN RAISE EXCEPTION 'w_listing_set: p_aliens has a NULL item' USING ERRCODE = '22023'; END IF;
  IF p_name_sorts IS NULL THEN RAISE EXCEPTION 'w_listing_set: p_name_sorts is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_name_sorts) > 5000 THEN RAISE EXCEPTION 'w_listing_set: p_name_sorts takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF v_org IS NULL THEN RETURN 0; END IF;       -- no org (or someone else's): no rows, the same answer
  DECLARE v_n integer;
  BEGIN
    IF cardinality(p_colls) <> cardinality(p_sks) OR cardinality(p_ids) <> cardinality(p_sks) OR cardinality(p_kinds) <> cardinality(p_sks) OR cardinality(p_folders) <> cardinality(p_sks) OR cardinality(p_hide_children) <> cardinality(p_sks) OR cardinality(p_aliens) <> cardinality(p_sks) OR cardinality(p_name_sorts) <> cardinality(p_sks) THEN
      RAISE EXCEPTION 'the arrays differ in length' USING ERRCODE = '22023';
    END IF;
    INSERT INTO kg.records (org_id, sk, coll, id, kind, folder, hide_children, alien, name_sort) SELECT v_org, k.a, k.b, k.c, k.d, k.e, k.f, k.g, k.h FROM unnest(p_sks, p_colls, p_ids, p_kinds, p_folders, p_hide_children, p_aliens, p_name_sorts) AS k(a, b, c, d, e, f, g, h)
      ON CONFLICT (org_id, sk) DO UPDATE SET folder = excluded.folder, hide_children = excluded.hide_children, alien = excluded.alien, name_sort = excluded.name_sort;
    GET DIAGNOSTICS v_n = ROW_COUNT;
    RETURN v_n;
  END;
END $fn$;
REVOKE ALL ON FUNCTION kg.w_listing_set(bigint[], smallint[], text[], smallint[], boolean[], boolean[], boolean[], bytea[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION kg.w_listing_set(bigint[], smallint[], text[], smallint[], boolean[], boolean[], boolean[], bytea[]) TO pipeshub_connectors;
SET LOCAL ROLE pipeshub_owner;
REVOKE CREATE ON SCHEMA kg FROM pipeshub_vetted_kg;
REVOKE pipeshub_vetted_kg FROM pipeshub_owner;
SELECT kernel.register_function('kg.w_listing_set(bigint[], smallint[], text[], smallint[], boolean[], boolean[], boolean[], bytea[])', 'write', 'pipeshub_connectors', 'org', false);
SELECT kernel.allow_vetted_write_op('kg.w_listing_set(bigint[], smallint[], text[], smallint[], boolean[], boolean[], boolean[], bytea[])', 'kg.records', 'upsert', NULL, ARRAY['folder', 'hide_children', 'alien', 'name_sort']::name[], NULL, 5000);
