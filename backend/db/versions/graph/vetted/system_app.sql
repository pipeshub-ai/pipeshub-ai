-- generated from the manifest by python -m db.lib.vetted render: edit the manifest, not this file
GRANT pipeshub_vetted_graph TO pipeshub_owner WITH SET TRUE;
GRANT USAGE ON SCHEMA graph TO pipeshub_vetted_graph;           -- the body names objects of its own schema
GRANT CREATE ON SCHEMA graph TO pipeshub_vetted_graph;
SET LOCAL ROLE pipeshub_vetted_graph;
CREATE FUNCTION graph.system_app(p_id text)
RETURNS TABLE (org_id text, app_id text, node_json text) LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, graph, pg_temp
SET standard_conforming_strings = on
AS $fn$
BEGIN
  IF p_id IS NULL THEN RAISE EXCEPTION 'system_app: p_id is required' USING ERRCODE = '22023'; END IF;
  IF length(p_id) NOT BETWEEN 1 AND 200 THEN RAISE EXCEPTION 'system_app: p_id length must be 1..200' USING ERRCODE = '22023'; END IF;
  RETURN QUERY SELECT n.org_id, n.id, n.doc::text FROM graph.nodes n WHERE n.coll = 'apps' AND n.id = p_id ORDER BY n.org_id LIMIT 1;
END $fn$;
REVOKE ALL ON FUNCTION graph.system_app(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION graph.system_app(text) TO pipeshub_connectors;
GRANT EXECUTE ON FUNCTION graph.system_app(text) TO pipeshub_indexing;
SET LOCAL ROLE pipeshub_owner;
REVOKE CREATE ON SCHEMA graph FROM pipeshub_vetted_graph;
REVOKE pipeshub_vetted_graph FROM pipeshub_owner;
SELECT kernel.register_function('graph.system_app(text)', 'read', NULL, 'global', true);
