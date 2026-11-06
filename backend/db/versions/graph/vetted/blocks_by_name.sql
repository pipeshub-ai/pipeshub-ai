-- generated from the manifest by python -m db.lib.vetted render: edit the manifest, not this file
GRANT pipeshub_vetted_graph TO pipeshub_owner WITH SET TRUE;
GRANT USAGE ON SCHEMA graph TO pipeshub_vetted_graph;           -- the body names objects of its own schema
GRANT CREATE ON SCHEMA graph TO pipeshub_vetted_graph;
SET LOCAL ROLE pipeshub_vetted_graph;
CREATE FUNCTION graph.blocks_by_name(p_connector_id text, p_name_folded text, p_keys text[], p_vals jsonb[], p_null_absent boolean, p_after text DEFAULT '', p_limit int DEFAULT 1000)
RETURNS TABLE (node_id text, doc jsonb) LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, graph, pg_temp
SET standard_conforming_strings = on
SET plan_cache_mode = force_custom_plan
AS $fn$
DECLARE v_org text := NULLIF(current_setting('app.org_id', true), '');
BEGIN
  IF p_connector_id IS NULL THEN RAISE EXCEPTION 'blocks_by_name: p_connector_id is required' USING ERRCODE = '22023'; END IF;
  IF length(p_connector_id) NOT BETWEEN 1 AND 200 THEN RAISE EXCEPTION 'blocks_by_name: p_connector_id length must be 1..200' USING ERRCODE = '22023'; END IF;
  IF p_name_folded IS NULL THEN RAISE EXCEPTION 'blocks_by_name: p_name_folded is required' USING ERRCODE = '22023'; END IF;
  IF length(p_name_folded) NOT BETWEEN 1 AND 1000 THEN RAISE EXCEPTION 'blocks_by_name: p_name_folded length must be 1..1000' USING ERRCODE = '22023'; END IF;
  IF p_keys IS NULL THEN RAISE EXCEPTION 'blocks_by_name: p_keys is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_keys) > 32 THEN RAISE EXCEPTION 'blocks_by_name: p_keys takes at most 32 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_keys) e WHERE e IS NULL) THEN RAISE EXCEPTION 'blocks_by_name: p_keys has a NULL item' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_keys) e WHERE e IS NOT NULL AND length(e) NOT BETWEEN 1 AND 200) THEN RAISE EXCEPTION 'blocks_by_name: p_keys has an item of the wrong length' USING ERRCODE = '22023'; END IF;
  IF p_vals IS NULL THEN RAISE EXCEPTION 'blocks_by_name: p_vals is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_vals) > 32 THEN RAISE EXCEPTION 'blocks_by_name: p_vals takes at most 32 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_vals) e WHERE e IS NULL) THEN RAISE EXCEPTION 'blocks_by_name: p_vals has a NULL item' USING ERRCODE = '22023'; END IF;
  IF p_null_absent IS NULL THEN RAISE EXCEPTION 'blocks_by_name: p_null_absent is required' USING ERRCODE = '22023'; END IF;
  IF p_after IS NULL THEN RAISE EXCEPTION 'blocks_by_name: p_after is required' USING ERRCODE = '22023'; END IF;
  IF length(p_after) NOT BETWEEN 0 AND 200 THEN RAISE EXCEPTION 'blocks_by_name: p_after length must be 0..200' USING ERRCODE = '22023'; END IF;
  IF p_limit IS NULL THEN RAISE EXCEPTION 'blocks_by_name: p_limit is required' USING ERRCODE = '22023'; END IF;
  IF p_limit NOT BETWEEN 1 AND 1000 THEN RAISE EXCEPTION 'blocks_by_name: p_limit must be 1..1000' USING ERRCODE = '22023'; END IF;
  IF v_org IS NULL THEN RETURN; END IF;       -- no org (or someone else's): no rows, the same answer
  IF cardinality(p_keys) <> cardinality(p_vals) THEN RAISE EXCEPTION 'p_keys and p_vals differ in length' USING ERRCODE = '22023'; END IF;
  RETURN QUERY SELECT n.id, n.doc FROM graph.nodes n
    WHERE n.org_id = v_org AND n.coll = 'blocks' AND n.doc->>'connectorId' = p_connector_id AND n.doc->>'nameFolded' = p_name_folded AND n.id > p_after
      AND NOT EXISTS (SELECT 1 FROM unnest(p_keys, p_vals) f(k, v)
        WHERE CASE WHEN p_null_absent AND f.v = 'null' THEN coalesce(n.doc->f.k, f.v) <> f.v
                   ELSE n.doc->f.k IS DISTINCT FROM f.v END)
    ORDER BY n.id LIMIT p_limit;
END $fn$;
REVOKE ALL ON FUNCTION graph.blocks_by_name(text, text, text[], jsonb[], boolean, text, int) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION graph.blocks_by_name(text, text, text[], jsonb[], boolean, text, int) TO pipeshub_connectors;
GRANT EXECUTE ON FUNCTION graph.blocks_by_name(text, text, text[], jsonb[], boolean, text, int) TO pipeshub_indexing;
GRANT EXECUTE ON FUNCTION graph.blocks_by_name(text, text, text[], jsonb[], boolean, text, int) TO pipeshub_query;
SET LOCAL ROLE pipeshub_owner;
REVOKE CREATE ON SCHEMA graph FROM pipeshub_vetted_graph;
REVOKE pipeshub_vetted_graph FROM pipeshub_owner;
SELECT kernel.register_function('graph.blocks_by_name(text, text, text[], jsonb[], boolean, text, int)', 'read', NULL, 'org', true);
