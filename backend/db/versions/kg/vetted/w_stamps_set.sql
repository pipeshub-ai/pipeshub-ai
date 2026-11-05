-- generated from the manifest by python -m db.lib.vetted render: edit the manifest, not this file
GRANT pipeshub_vetted_kg TO pipeshub_owner WITH SET TRUE;
GRANT USAGE ON SCHEMA kg TO pipeshub_vetted_kg;           -- the body names objects of its own schema
GRANT CREATE ON SCHEMA kg TO pipeshub_vetted_kg;
SET LOCAL ROLE pipeshub_vetted_kg;
CREATE FUNCTION kg.w_stamps_set(p_sks bigint[], p_colls smallint[], p_ids text[], p_kinds smallint[], p_paths bytea[], p_classes bigint[], p_self_lits boolean[], p_seeds boolean[], p_own_grants boolean[], p_under_deleted boolean[], p_legacy smallint[], p_leaves boolean[])
RETURNS int LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, kg, pg_temp
SET standard_conforming_strings = on
SET plan_cache_mode = force_custom_plan
AS $fn$
DECLARE v_org text := NULLIF(current_setting('app.org_id', true), '');
BEGIN
  IF p_sks IS NULL THEN RAISE EXCEPTION 'w_stamps_set: p_sks is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_sks) > 5000 THEN RAISE EXCEPTION 'w_stamps_set: p_sks takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_sks) e WHERE e IS NULL) THEN RAISE EXCEPTION 'w_stamps_set: p_sks has a NULL item' USING ERRCODE = '22023'; END IF;
  IF p_colls IS NULL THEN RAISE EXCEPTION 'w_stamps_set: p_colls is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_colls) > 5000 THEN RAISE EXCEPTION 'w_stamps_set: p_colls takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_colls) e WHERE e IS NULL) THEN RAISE EXCEPTION 'w_stamps_set: p_colls has a NULL item' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_colls) e WHERE e IS NOT NULL AND e NOT BETWEEN 1 AND 3) THEN RAISE EXCEPTION 'w_stamps_set: p_colls has an item out of range' USING ERRCODE = '22023'; END IF;
  IF p_ids IS NULL THEN RAISE EXCEPTION 'w_stamps_set: p_ids is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_ids) > 5000 THEN RAISE EXCEPTION 'w_stamps_set: p_ids takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_ids) e WHERE e IS NULL) THEN RAISE EXCEPTION 'w_stamps_set: p_ids has a NULL item' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_ids) e WHERE e IS NOT NULL AND length(e) NOT BETWEEN 1 AND 256) THEN RAISE EXCEPTION 'w_stamps_set: p_ids has an item of the wrong length' USING ERRCODE = '22023'; END IF;
  IF p_kinds IS NULL THEN RAISE EXCEPTION 'w_stamps_set: p_kinds is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_kinds) > 5000 THEN RAISE EXCEPTION 'w_stamps_set: p_kinds takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_kinds) e WHERE e IS NULL) THEN RAISE EXCEPTION 'w_stamps_set: p_kinds has a NULL item' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_kinds) e WHERE e IS NOT NULL AND e NOT BETWEEN 1 AND 3) THEN RAISE EXCEPTION 'w_stamps_set: p_kinds has an item out of range' USING ERRCODE = '22023'; END IF;
  IF p_paths IS NULL THEN RAISE EXCEPTION 'w_stamps_set: p_paths is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_paths) > 5000 THEN RAISE EXCEPTION 'w_stamps_set: p_paths takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF p_classes IS NULL THEN RAISE EXCEPTION 'w_stamps_set: p_classes is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_classes) > 5000 THEN RAISE EXCEPTION 'w_stamps_set: p_classes takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF p_self_lits IS NULL THEN RAISE EXCEPTION 'w_stamps_set: p_self_lits is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_self_lits) > 5000 THEN RAISE EXCEPTION 'w_stamps_set: p_self_lits takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_self_lits) e WHERE e IS NULL) THEN RAISE EXCEPTION 'w_stamps_set: p_self_lits has a NULL item' USING ERRCODE = '22023'; END IF;
  IF p_seeds IS NULL THEN RAISE EXCEPTION 'w_stamps_set: p_seeds is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_seeds) > 5000 THEN RAISE EXCEPTION 'w_stamps_set: p_seeds takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_seeds) e WHERE e IS NULL) THEN RAISE EXCEPTION 'w_stamps_set: p_seeds has a NULL item' USING ERRCODE = '22023'; END IF;
  IF p_own_grants IS NULL THEN RAISE EXCEPTION 'w_stamps_set: p_own_grants is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_own_grants) > 5000 THEN RAISE EXCEPTION 'w_stamps_set: p_own_grants takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_own_grants) e WHERE e IS NULL) THEN RAISE EXCEPTION 'w_stamps_set: p_own_grants has a NULL item' USING ERRCODE = '22023'; END IF;
  IF p_under_deleted IS NULL THEN RAISE EXCEPTION 'w_stamps_set: p_under_deleted is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_under_deleted) > 5000 THEN RAISE EXCEPTION 'w_stamps_set: p_under_deleted takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_under_deleted) e WHERE e IS NULL) THEN RAISE EXCEPTION 'w_stamps_set: p_under_deleted has a NULL item' USING ERRCODE = '22023'; END IF;
  IF p_legacy IS NULL THEN RAISE EXCEPTION 'w_stamps_set: p_legacy is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_legacy) > 5000 THEN RAISE EXCEPTION 'w_stamps_set: p_legacy takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_legacy) e WHERE e IS NOT NULL AND e NOT BETWEEN 0 AND 8) THEN RAISE EXCEPTION 'w_stamps_set: p_legacy has an item out of range' USING ERRCODE = '22023'; END IF;
  IF p_leaves IS NULL THEN RAISE EXCEPTION 'w_stamps_set: p_leaves is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_leaves) > 5000 THEN RAISE EXCEPTION 'w_stamps_set: p_leaves takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_leaves) e WHERE e IS NULL) THEN RAISE EXCEPTION 'w_stamps_set: p_leaves has a NULL item' USING ERRCODE = '22023'; END IF;
  IF v_org IS NULL THEN RETURN 0; END IF;       -- no org (or someone else's): no rows, the same answer
  DECLARE v_n integer;
  BEGIN
    IF cardinality(p_colls) <> cardinality(p_sks) OR cardinality(p_ids) <> cardinality(p_sks) OR cardinality(p_kinds) <> cardinality(p_sks) OR cardinality(p_paths) <> cardinality(p_sks) OR cardinality(p_classes) <> cardinality(p_sks) OR cardinality(p_self_lits) <> cardinality(p_sks) OR cardinality(p_seeds) <> cardinality(p_sks) OR cardinality(p_own_grants) <> cardinality(p_sks) OR cardinality(p_under_deleted) <> cardinality(p_sks) OR cardinality(p_legacy) <> cardinality(p_sks) OR cardinality(p_leaves) <> cardinality(p_sks) THEN
      RAISE EXCEPTION 'the arrays differ in length' USING ERRCODE = '22023';
    END IF;
    INSERT INTO kg.records (org_id, sk, coll, id, kind, path, class_id, self_lit, seed, own_grant, under_deleted, legacy_reason, leaf) SELECT v_org, k.a, k.b, k.c, k.d, k.e, k.f, k.g, k.h, k.i, k.j, k.k, k.l FROM unnest(p_sks, p_colls, p_ids, p_kinds, p_paths, p_classes, p_self_lits, p_seeds, p_own_grants, p_under_deleted, p_legacy, p_leaves) AS k(a, b, c, d, e, f, g, h, i, j, k, l)
      ON CONFLICT (org_id, sk) DO UPDATE SET path = excluded.path, class_id = excluded.class_id, self_lit = excluded.self_lit, seed = excluded.seed, own_grant = excluded.own_grant, under_deleted = excluded.under_deleted, legacy_reason = excluded.legacy_reason, leaf = excluded.leaf;
    GET DIAGNOSTICS v_n = ROW_COUNT;
    RETURN v_n;
  END;
END $fn$;
REVOKE ALL ON FUNCTION kg.w_stamps_set(bigint[], smallint[], text[], smallint[], bytea[], bigint[], boolean[], boolean[], boolean[], boolean[], smallint[], boolean[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION kg.w_stamps_set(bigint[], smallint[], text[], smallint[], bytea[], bigint[], boolean[], boolean[], boolean[], boolean[], smallint[], boolean[]) TO pipeshub_connectors;
SET LOCAL ROLE pipeshub_owner;
REVOKE CREATE ON SCHEMA kg FROM pipeshub_vetted_kg;
REVOKE pipeshub_vetted_kg FROM pipeshub_owner;
SELECT kernel.register_function('kg.w_stamps_set(bigint[], smallint[], text[], smallint[], bytea[], bigint[], boolean[], boolean[], boolean[], boolean[], smallint[], boolean[])', 'write', 'pipeshub_connectors', 'org', false);
SELECT kernel.allow_vetted_write_op('kg.w_stamps_set(bigint[], smallint[], text[], smallint[], bytea[], bigint[], boolean[], boolean[], boolean[], boolean[], smallint[], boolean[])', 'kg.records', 'upsert', NULL, ARRAY['path', 'class_id', 'self_lit', 'seed', 'own_grant', 'under_deleted', 'legacy_reason', 'leaf']::name[], NULL, 5000);
