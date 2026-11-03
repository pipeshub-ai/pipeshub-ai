-- generated from the manifest by python -m db.lib.vetted render: edit the manifest, not this file
GRANT pipeshub_vetted_kg TO pipeshub_owner WITH SET TRUE;
GRANT USAGE ON SCHEMA kg TO pipeshub_vetted_kg;           -- the body names objects of its own schema
GRANT CREATE ON SCHEMA kg TO pipeshub_vetted_kg;
SET LOCAL ROLE pipeshub_vetted_kg;
CREATE FUNCTION kg.w_nodes_upsert(p_colls smallint[], p_ids text[])
RETURNS TABLE (o_coll smallint, o_id text, o_sk bigint) LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, kg, pg_temp
SET standard_conforming_strings = on
SET plan_cache_mode = force_custom_plan
AS $fn$
DECLARE v_org text := NULLIF(current_setting('app.org_id', true), '');
BEGIN
  IF p_colls IS NULL THEN RAISE EXCEPTION 'w_nodes_upsert: p_colls is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_colls) > 5000 THEN RAISE EXCEPTION 'w_nodes_upsert: p_colls takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_colls) e WHERE e IS NULL) THEN RAISE EXCEPTION 'w_nodes_upsert: p_colls has a NULL item' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_colls) e WHERE e IS NOT NULL AND e NOT BETWEEN 1 AND 8) THEN RAISE EXCEPTION 'w_nodes_upsert: p_colls has an item out of range' USING ERRCODE = '22023'; END IF;
  IF p_ids IS NULL THEN RAISE EXCEPTION 'w_nodes_upsert: p_ids is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_ids) > 5000 THEN RAISE EXCEPTION 'w_nodes_upsert: p_ids takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_ids) e WHERE e IS NULL) THEN RAISE EXCEPTION 'w_nodes_upsert: p_ids has a NULL item' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_ids) e WHERE e IS NOT NULL AND length(e) NOT BETWEEN 1 AND 256) THEN RAISE EXCEPTION 'w_nodes_upsert: p_ids has an item of the wrong length' USING ERRCODE = '22023'; END IF;
  IF v_org IS NULL THEN RETURN; END IF;       -- no org (or someone else's): no rows, the same answer
  BEGIN
    IF cardinality(p_colls) <> cardinality(p_ids) THEN
      RAISE EXCEPTION 'the arrays differ in length' USING ERRCODE = '22023';
    END IF;
    INSERT INTO kg.node_keys (org_id, coll, id) SELECT v_org, k.a, k.b FROM unnest(p_colls, p_ids) AS k(a, b)
      ON CONFLICT (org_id, coll, id) DO NOTHING;
    RETURN QUERY SELECT k.coll, k.id, k.sk FROM kg.node_keys k JOIN unnest(p_colls, p_ids) AS x(c, i) ON k.coll = x.c AND k.id = x.i WHERE k.org_id = v_org LIMIT 5000;
  END;
END $fn$;
REVOKE ALL ON FUNCTION kg.w_nodes_upsert(smallint[], text[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION kg.w_nodes_upsert(smallint[], text[]) TO pipeshub_connectors;
SET LOCAL ROLE pipeshub_owner;
REVOKE CREATE ON SCHEMA kg FROM pipeshub_vetted_kg;
REVOKE pipeshub_vetted_kg FROM pipeshub_owner;
SELECT kernel.register_function('kg.w_nodes_upsert(smallint[], text[])', 'write', 'pipeshub_connectors', 'org', false);
SELECT kernel.allow_vetted_write_op('kg.w_nodes_upsert(smallint[], text[])', 'kg.node_keys', 'upsert', NULL, ARRAY[]::name[], NULL, 5000);
