-- generated from the manifest by python -m db.lib.vetted render: edit the manifest, not this file
GRANT pipeshub_vetted_graph TO pipeshub_owner WITH SET TRUE;
GRANT USAGE ON SCHEMA graph TO pipeshub_vetted_graph;           -- the body names objects of its own schema
GRANT CREATE ON SCHEMA graph TO pipeshub_vetted_graph;
SET LOCAL ROLE pipeshub_vetted_graph;
CREATE FUNCTION graph.stage_states_page(p_connector_id text, p_stage text, p_status text, p_after text DEFAULT '', p_limit int DEFAULT 1000)
RETURNS TABLE (node_id text, doc jsonb) LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, graph, pg_temp
SET standard_conforming_strings = on
SET plan_cache_mode = force_custom_plan
AS $fn$
DECLARE v_org text := NULLIF(current_setting('app.org_id', true), '');
BEGIN
  IF p_connector_id IS NULL THEN RAISE EXCEPTION 'stage_states_page: p_connector_id is required' USING ERRCODE = '22023'; END IF;
  IF length(p_connector_id) NOT BETWEEN 1 AND 200 THEN RAISE EXCEPTION 'stage_states_page: p_connector_id length must be 1..200' USING ERRCODE = '22023'; END IF;
  IF p_stage IS NULL THEN RAISE EXCEPTION 'stage_states_page: p_stage is required' USING ERRCODE = '22023'; END IF;
  IF length(p_stage) NOT BETWEEN 1 AND 200 THEN RAISE EXCEPTION 'stage_states_page: p_stage length must be 1..200' USING ERRCODE = '22023'; END IF;
  IF p_status IS NULL THEN RAISE EXCEPTION 'stage_states_page: p_status is required' USING ERRCODE = '22023'; END IF;
  IF length(p_status) NOT BETWEEN 1 AND 200 THEN RAISE EXCEPTION 'stage_states_page: p_status length must be 1..200' USING ERRCODE = '22023'; END IF;
  IF p_after IS NULL THEN RAISE EXCEPTION 'stage_states_page: p_after is required' USING ERRCODE = '22023'; END IF;
  IF length(p_after) NOT BETWEEN 0 AND 200 THEN RAISE EXCEPTION 'stage_states_page: p_after length must be 0..200' USING ERRCODE = '22023'; END IF;
  IF p_limit IS NULL THEN RAISE EXCEPTION 'stage_states_page: p_limit is required' USING ERRCODE = '22023'; END IF;
  IF p_limit NOT BETWEEN 1 AND 1000 THEN RAISE EXCEPTION 'stage_states_page: p_limit must be 1..1000' USING ERRCODE = '22023'; END IF;
  IF v_org IS NULL THEN RETURN; END IF;       -- no org (or someone else's): no rows, the same answer
  RETURN QUERY SELECT n.id, n.doc FROM graph.nodes n WHERE n.org_id = v_org AND n.coll = 'stageStates' AND n.doc->>'connectorId' = p_connector_id
      AND n.doc->>'stage' = p_stage AND n.doc->>'status' = p_status AND (p_after = '' OR n.id > p_after)
    ORDER BY n.doc->>'connectorId', n.doc->>'stage', n.doc->>'status', n.id LIMIT p_limit;      -- the key in the order: the planner would sort the whole page's states otherwise
END $fn$;
REVOKE ALL ON FUNCTION graph.stage_states_page(text, text, text, text, int) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION graph.stage_states_page(text, text, text, text, int) TO pipeshub_connectors;
GRANT EXECUTE ON FUNCTION graph.stage_states_page(text, text, text, text, int) TO pipeshub_indexing;
GRANT EXECUTE ON FUNCTION graph.stage_states_page(text, text, text, text, int) TO pipeshub_query;
SET LOCAL ROLE pipeshub_owner;
REVOKE CREATE ON SCHEMA graph FROM pipeshub_vetted_graph;
REVOKE pipeshub_vetted_graph FROM pipeshub_owner;
SELECT kernel.register_function('graph.stage_states_page(text, text, text, text, int)', 'read', NULL, 'org', true);
