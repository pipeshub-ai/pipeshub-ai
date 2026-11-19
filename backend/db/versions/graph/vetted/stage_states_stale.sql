-- generated from the manifest by python -m db.lib.vetted render: edit the manifest, not this file
GRANT pipeshub_vetted_graph TO pipeshub_owner WITH SET TRUE;
GRANT USAGE ON SCHEMA graph TO pipeshub_vetted_graph;           -- the body names objects of its own schema
GRANT CREATE ON SCHEMA graph TO pipeshub_vetted_graph;
SET LOCAL ROLE pipeshub_vetted_graph;
CREATE FUNCTION graph.stage_states_stale(p_statuses text[], p_before bigint, p_limit int)
RETURNS TABLE (node_id text, doc jsonb) LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, graph, pg_temp
SET standard_conforming_strings = on
SET plan_cache_mode = force_custom_plan
AS $fn$
DECLARE v_org text := NULLIF(current_setting('app.org_id', true), '');
BEGIN
  IF p_statuses IS NULL THEN RAISE EXCEPTION 'stage_states_stale: p_statuses is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_statuses) > 20 THEN RAISE EXCEPTION 'stage_states_stale: p_statuses takes at most 20 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_statuses) e WHERE e IS NULL) THEN RAISE EXCEPTION 'stage_states_stale: p_statuses has a NULL item' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_statuses) e WHERE e IS NOT NULL AND length(e) NOT BETWEEN 1 AND 100) THEN RAISE EXCEPTION 'stage_states_stale: p_statuses has an item of the wrong length' USING ERRCODE = '22023'; END IF;
  IF p_before IS NULL THEN RAISE EXCEPTION 'stage_states_stale: p_before is required' USING ERRCODE = '22023'; END IF;
  IF p_limit IS NULL THEN RAISE EXCEPTION 'stage_states_stale: p_limit is required' USING ERRCODE = '22023'; END IF;
  IF p_limit NOT BETWEEN 1 AND 1000 THEN RAISE EXCEPTION 'stage_states_stale: p_limit must be 1..1000' USING ERRCODE = '22023'; END IF;
  IF v_org IS NULL THEN RETURN; END IF;       -- no org (or someone else's): no rows, the same answer
  RETURN QUERY SELECT x.id, x.doc FROM (SELECT DISTINCT e FROM unnest(p_statuses) e) st
    CROSS JOIN LATERAL (SELECT n.id, n.doc FROM graph.nodes n WHERE n.org_id = v_org AND n.coll = 'stageStates' AND n.doc->>'status' = st.e
        AND n.doc->'updatedAtMs' < to_jsonb(p_before) ORDER BY n.doc->>'status', n.doc->'updatedAtMs' LIMIT p_limit) x
    ORDER BY x.doc->'updatedAtMs' LIMIT p_limit;      -- the status in the order: without it the planner sorts every state of the status
END $fn$;
REVOKE ALL ON FUNCTION graph.stage_states_stale(text[], bigint, int) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION graph.stage_states_stale(text[], bigint, int) TO pipeshub_connectors;
GRANT EXECUTE ON FUNCTION graph.stage_states_stale(text[], bigint, int) TO pipeshub_indexing;
GRANT EXECUTE ON FUNCTION graph.stage_states_stale(text[], bigint, int) TO pipeshub_query;
SET LOCAL ROLE pipeshub_owner;
REVOKE CREATE ON SCHEMA graph FROM pipeshub_vetted_graph;
REVOKE pipeshub_vetted_graph FROM pipeshub_owner;
SELECT kernel.register_function('graph.stage_states_stale(text[], bigint, int)', 'read', NULL, 'org', true);
