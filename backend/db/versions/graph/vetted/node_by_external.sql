-- generated from the manifest by python -m db.lib.vetted render: edit the manifest, not this file
GRANT pipeshub_vetted_graph TO pipeshub_owner WITH SET TRUE;
GRANT USAGE ON SCHEMA graph TO pipeshub_vetted_graph;           -- the body names objects of its own schema
GRANT CREATE ON SCHEMA graph TO pipeshub_vetted_graph;
SET LOCAL ROLE pipeshub_vetted_graph;
CREATE FUNCTION graph.node_by_external(p_coll text, p_connector_id text, p_external_id text)
RETURNS TABLE (node_id text, doc jsonb) LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, graph, pg_temp
SET standard_conforming_strings = on
SET plan_cache_mode = force_custom_plan
AS $fn$
DECLARE v_org text := NULLIF(current_setting('app.org_id', true), '');
BEGIN
  IF p_coll IS NULL THEN RAISE EXCEPTION 'node_by_external: p_coll is required' USING ERRCODE = '22023'; END IF;
  IF p_coll NOT IN ('groups', 'recordGroups', 'roles') THEN RAISE EXCEPTION 'node_by_external: p_coll is not one of the allowed values' USING ERRCODE = '22023'; END IF;
  IF p_connector_id IS NULL THEN RAISE EXCEPTION 'node_by_external: p_connector_id is required' USING ERRCODE = '22023'; END IF;
  IF length(p_connector_id) NOT BETWEEN 1 AND 200 THEN RAISE EXCEPTION 'node_by_external: p_connector_id length must be 1..200' USING ERRCODE = '22023'; END IF;
  IF p_external_id IS NULL THEN RAISE EXCEPTION 'node_by_external: p_external_id is required' USING ERRCODE = '22023'; END IF;
  IF length(p_external_id) NOT BETWEEN 1 AND 2000 THEN RAISE EXCEPTION 'node_by_external: p_external_id length must be 1..2000' USING ERRCODE = '22023'; END IF;
  IF v_org IS NULL THEN RETURN; END IF;       -- no org (or someone else's): no rows, the same answer
  -- one branch for each collection, so that the partial index of the collection matches
  RETURN QUERY (SELECT n.id, n.doc FROM graph.nodes n WHERE p_coll = 'groups' AND n.org_id = v_org AND n.coll = 'groups'
      AND n.doc->>'connectorId' = p_connector_id AND n.doc->>'externalGroupId' = p_external_id LIMIT 1)
    UNION ALL (SELECT n.id, n.doc FROM graph.nodes n WHERE p_coll = 'recordGroups' AND n.org_id = v_org AND n.coll = 'recordGroups'
      AND n.doc->>'connectorId' = p_connector_id AND n.doc->>'externalGroupId' = p_external_id LIMIT 1)
    UNION ALL (SELECT n.id, n.doc FROM graph.nodes n WHERE p_coll = 'roles' AND n.org_id = v_org AND n.coll = 'roles'
      AND n.doc->>'connectorId' = p_connector_id AND n.doc->>'externalRoleId' = p_external_id LIMIT 1);
END $fn$;
REVOKE ALL ON FUNCTION graph.node_by_external(text, text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION graph.node_by_external(text, text, text) TO pipeshub_connectors;
GRANT EXECUTE ON FUNCTION graph.node_by_external(text, text, text) TO pipeshub_indexing;
GRANT EXECUTE ON FUNCTION graph.node_by_external(text, text, text) TO pipeshub_query;
SET LOCAL ROLE pipeshub_owner;
REVOKE CREATE ON SCHEMA graph FROM pipeshub_vetted_graph;
REVOKE pipeshub_vetted_graph FROM pipeshub_owner;
SELECT kernel.register_function('graph.node_by_external(text, text, text)', 'read', NULL, 'org', true);
