-- generated from the manifest by python -m db.lib.vetted render: edit the manifest, not this file
GRANT pipeshub_vetted_graph TO pipeshub_owner WITH SET TRUE;
GRANT USAGE ON SCHEMA graph TO pipeshub_vetted_graph;           -- the body names objects of its own schema
GRANT CREATE ON SCHEMA graph TO pipeshub_vetted_graph;
SET LOCAL ROLE pipeshub_vetted_graph;
CREATE FUNCTION graph.records_by_name(p_connector_id text, p_pattern text, p_limit int)
RETURNS TABLE (coll text, node_id text) LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, graph, pg_temp
SET standard_conforming_strings = on
SET plan_cache_mode = force_custom_plan
AS $fn$
DECLARE v_org text := NULLIF(current_setting('app.org_id', true), '');
BEGIN
  IF p_connector_id IS NULL THEN RAISE EXCEPTION 'records_by_name: p_connector_id is required' USING ERRCODE = '22023'; END IF;
  IF length(p_connector_id) NOT BETWEEN 1 AND 200 THEN RAISE EXCEPTION 'records_by_name: p_connector_id length must be 1..200' USING ERRCODE = '22023'; END IF;
  IF p_pattern IS NULL THEN RAISE EXCEPTION 'records_by_name: p_pattern is required' USING ERRCODE = '22023'; END IF;
  IF length(p_pattern) NOT BETWEEN 5 AND 2000 THEN RAISE EXCEPTION 'records_by_name: p_pattern length must be 5..2000' USING ERRCODE = '22023'; END IF;
  IF p_pattern !~ '^(?=.*[0-9a-hjl-z]{3})%([0-9a-hjl-z]+%)+$' THEN RAISE EXCEPTION 'records_by_name: p_pattern does not match the expected format' USING ERRCODE = '22023'; END IF;
  IF p_limit IS NULL THEN RAISE EXCEPTION 'records_by_name: p_limit is required' USING ERRCODE = '22023'; END IF;
  IF p_limit NOT BETWEEN 1 AND 100000 THEN RAISE EXCEPTION 'records_by_name: p_limit must be 1..100000' USING ERRCODE = '22023'; END IF;
  IF v_org IS NULL THEN RETURN; END IF;       -- no org (or someone else's): no rows, the same answer
  RETURN QUERY SELECT n.coll, n.id FROM graph.nodes n WHERE n.org_id = v_org AND n.coll IN ('records', 'recordGroups') AND n.doc->>'connectorId' = p_connector_id
    AND (coalesce(n.doc->>'name', '') || ' ' || coalesce(n.doc->>'recordName', '') || ' ' || coalesce(n.doc->>'groupName', '')) ILIKE p_pattern LIMIT p_limit;
END $fn$;
REVOKE ALL ON FUNCTION graph.records_by_name(text, text, int) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION graph.records_by_name(text, text, int) TO pipeshub_connectors;
GRANT EXECUTE ON FUNCTION graph.records_by_name(text, text, int) TO pipeshub_indexing;
GRANT EXECUTE ON FUNCTION graph.records_by_name(text, text, int) TO pipeshub_query;
SET LOCAL ROLE pipeshub_owner;
REVOKE CREATE ON SCHEMA graph FROM pipeshub_vetted_graph;
REVOKE pipeshub_vetted_graph FROM pipeshub_owner;
SELECT kernel.register_function('graph.records_by_name(text, text, int)', 'read', NULL, 'org', false);
