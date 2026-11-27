-- generated from the manifest by python -m db.lib.vetted render: edit the manifest, not this file
GRANT pipeshub_vetted_graph TO pipeshub_owner WITH SET TRUE;
GRANT USAGE ON SCHEMA graph TO pipeshub_vetted_graph;           -- the body names objects of its own schema
GRANT CREATE ON SCHEMA graph TO pipeshub_vetted_graph;
SET LOCAL ROLE pipeshub_vetted_graph;
CREATE FUNCTION graph.records_needing_projection(p_connector_id text, p_props_hash text, p_props_gen bigint, p_after text DEFAULT '', p_limit int DEFAULT 500)
RETURNS TABLE (node_id text, doc jsonb) LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, graph, pg_temp
SET standard_conforming_strings = on
SET plan_cache_mode = force_custom_plan
AS $fn$
DECLARE v_org text := NULLIF(current_setting('app.org_id', true), '');
BEGIN
  IF p_connector_id IS NULL THEN RAISE EXCEPTION 'records_needing_projection: p_connector_id is required' USING ERRCODE = '22023'; END IF;
  IF length(p_connector_id) NOT BETWEEN 1 AND 200 THEN RAISE EXCEPTION 'records_needing_projection: p_connector_id length must be 1..200' USING ERRCODE = '22023'; END IF;
  IF p_props_hash IS NULL THEN RAISE EXCEPTION 'records_needing_projection: p_props_hash is required' USING ERRCODE = '22023'; END IF;
  IF length(p_props_hash) NOT BETWEEN 1 AND 200 THEN RAISE EXCEPTION 'records_needing_projection: p_props_hash length must be 1..200' USING ERRCODE = '22023'; END IF;
  IF p_props_gen IS NULL THEN RAISE EXCEPTION 'records_needing_projection: p_props_gen is required' USING ERRCODE = '22023'; END IF;
  IF p_props_gen NOT BETWEEN 0 AND 2147483647 THEN RAISE EXCEPTION 'records_needing_projection: p_props_gen must be 0..2147483647' USING ERRCODE = '22023'; END IF;
  IF p_after IS NULL THEN RAISE EXCEPTION 'records_needing_projection: p_after is required' USING ERRCODE = '22023'; END IF;
  IF length(p_after) NOT BETWEEN 0 AND 200 THEN RAISE EXCEPTION 'records_needing_projection: p_after length must be 0..200' USING ERRCODE = '22023'; END IF;
  IF p_limit IS NULL THEN RAISE EXCEPTION 'records_needing_projection: p_limit is required' USING ERRCODE = '22023'; END IF;
  IF p_limit NOT BETWEEN 1 AND 1000 THEN RAISE EXCEPTION 'records_needing_projection: p_limit must be 1..1000' USING ERRCODE = '22023'; END IF;
  IF v_org IS NULL THEN RETURN; END IF;       -- no org (or someone else's): no rows, the same answer
  RETURN QUERY SELECT n.id, n.doc FROM graph.nodes n
    CROSS JOIN LATERAL (SELECT CASE WHEN jsonb_typeof(n.doc->'sourceLastModifiedTimestamp') IS NULL OR jsonb_typeof(n.doc->'sourceLastModifiedTimestamp') = 'null'
        THEN n.doc->'updatedAtTimestamp' ELSE n.doc->'sourceLastModifiedTimestamp' END AS s) st
    WHERE n.org_id = v_org AND n.coll = 'records' AND n.doc->>'connectorId' = p_connector_id
      AND (p_after = '' OR n.id COLLATE ucs_basic > p_after COLLATE ucs_basic)
      AND n.doc->'isDeleted' IS DISTINCT FROM to_jsonb(true)
      AND jsonb_typeof(st.s) = 'number' AND st.s > to_jsonb(0)
      AND (n.doc->'propsSet' IS DISTINCT FROM to_jsonb(p_props_hash)
           OR jsonb_typeof(n.doc->'propsGen') IS DISTINCT FROM 'number' OR n.doc->'propsGen' < to_jsonb(p_props_gen)
           OR jsonb_typeof(n.doc->'propsSrc') IS DISTINCT FROM 'number' OR n.doc->'propsSrc' < st.s)
    ORDER BY n.doc->>'connectorId', n.id COLLATE ucs_basic LIMIT p_limit;      -- the connector in the order, as records_page_by_connector
END $fn$;
REVOKE ALL ON FUNCTION graph.records_needing_projection(text, text, bigint, text, int) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION graph.records_needing_projection(text, text, bigint, text, int) TO pipeshub_connectors;
GRANT EXECUTE ON FUNCTION graph.records_needing_projection(text, text, bigint, text, int) TO pipeshub_indexing;
SET LOCAL ROLE pipeshub_owner;
REVOKE CREATE ON SCHEMA graph FROM pipeshub_vetted_graph;
REVOKE pipeshub_vetted_graph FROM pipeshub_owner;
SELECT kernel.register_function('graph.records_needing_projection(text, text, bigint, text, int)', 'read', NULL, 'org', true);
