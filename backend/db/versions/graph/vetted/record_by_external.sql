-- generated from the manifest by python -m db.lib.vetted render: edit the manifest, not this file
GRANT pipeshub_vetted_graph TO pipeshub_owner WITH SET TRUE;
GRANT USAGE ON SCHEMA graph TO pipeshub_vetted_graph;           -- the body names objects of its own schema
GRANT CREATE ON SCHEMA graph TO pipeshub_vetted_graph;
SET LOCAL ROLE pipeshub_vetted_graph;
CREATE FUNCTION graph.record_by_external(p_connector_id text, p_external_id text)
RETURNS TABLE (node_id text, doc jsonb) LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, graph, pg_temp
SET standard_conforming_strings = on
SET plan_cache_mode = force_custom_plan
AS $fn$
DECLARE v_org text := NULLIF(current_setting('app.org_id', true), '');
BEGIN
  IF p_connector_id IS NULL THEN RAISE EXCEPTION 'record_by_external: p_connector_id is required' USING ERRCODE = '22023'; END IF;
  IF length(p_connector_id) NOT BETWEEN 1 AND 200 THEN RAISE EXCEPTION 'record_by_external: p_connector_id length must be 1..200' USING ERRCODE = '22023'; END IF;
  IF p_external_id IS NULL THEN RAISE EXCEPTION 'record_by_external: p_external_id is required' USING ERRCODE = '22023'; END IF;
  IF length(p_external_id) NOT BETWEEN 1 AND 2000 THEN RAISE EXCEPTION 'record_by_external: p_external_id length must be 1..2000' USING ERRCODE = '22023'; END IF;
  IF v_org IS NULL THEN RETURN; END IF;       -- no org (or someone else's): no rows, the same answer
  RETURN QUERY SELECT n.id, n.doc FROM graph.nodes n
    WHERE n.org_id = v_org AND n.coll = 'records' AND n.doc->>'connectorId' = p_connector_id AND n.doc->>'externalRecordId' = p_external_id
    LIMIT 100;
END $fn$;
REVOKE ALL ON FUNCTION graph.record_by_external(text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION graph.record_by_external(text, text) TO pipeshub_connectors;
GRANT EXECUTE ON FUNCTION graph.record_by_external(text, text) TO pipeshub_indexing;
GRANT EXECUTE ON FUNCTION graph.record_by_external(text, text) TO pipeshub_query;
SET LOCAL ROLE pipeshub_owner;
REVOKE CREATE ON SCHEMA graph FROM pipeshub_vetted_graph;
REVOKE pipeshub_vetted_graph FROM pipeshub_owner;
SELECT kernel.register_function('graph.record_by_external(text, text)', 'read', NULL, 'org', true);
