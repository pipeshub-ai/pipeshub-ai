-- generated from the manifest by python -m db.lib.vetted render: edit the manifest, not this file
GRANT pipeshub_vetted_graph TO pipeshub_owner WITH SET TRUE;
GRANT USAGE ON SCHEMA graph TO pipeshub_vetted_graph;           -- the body names objects of its own schema
GRANT CREATE ON SCHEMA graph TO pipeshub_vetted_graph;
SET LOCAL ROLE pipeshub_vetted_graph;
CREATE FUNCTION graph.system_orgs(p_all boolean, p_external boolean, p_after text DEFAULT '', p_limit int DEFAULT 1000)
RETURNS TABLE (org_id text, node_json text) LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, graph, pg_temp
SET standard_conforming_strings = on
AS $fn$
BEGIN
  IF p_all IS NULL THEN RAISE EXCEPTION 'system_orgs: p_all is required' USING ERRCODE = '22023'; END IF;
  IF p_external IS NULL THEN RAISE EXCEPTION 'system_orgs: p_external is required' USING ERRCODE = '22023'; END IF;
  IF p_after IS NULL THEN RAISE EXCEPTION 'system_orgs: p_after is required' USING ERRCODE = '22023'; END IF;
  IF length(p_after) NOT BETWEEN 0 AND 200 THEN RAISE EXCEPTION 'system_orgs: p_after length must be 0..200' USING ERRCODE = '22023'; END IF;
  IF p_limit IS NULL THEN RAISE EXCEPTION 'system_orgs: p_limit is required' USING ERRCODE = '22023'; END IF;
  IF p_limit NOT BETWEEN 1 AND 1000 THEN RAISE EXCEPTION 'system_orgs: p_limit must be 1..1000' USING ERRCODE = '22023'; END IF;
  RETURN QUERY SELECT n.org_id, n.doc::text FROM graph.nodes n
    WHERE n.coll = 'organizations' AND (n.doc @> '{"isExternal": true}') = p_external
      AND (p_all OR n.doc @> '{"isActive": true}') AND n.org_id > p_after ORDER BY n.org_id LIMIT p_limit;
END $fn$;
REVOKE ALL ON FUNCTION graph.system_orgs(boolean, boolean, text, int) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION graph.system_orgs(boolean, boolean, text, int) TO pipeshub_connectors;
GRANT EXECUTE ON FUNCTION graph.system_orgs(boolean, boolean, text, int) TO pipeshub_indexing;
GRANT EXECUTE ON FUNCTION graph.system_orgs(boolean, boolean, text, int) TO pipeshub_query;
SET LOCAL ROLE pipeshub_owner;
REVOKE CREATE ON SCHEMA graph FROM pipeshub_vetted_graph;
REVOKE pipeshub_vetted_graph FROM pipeshub_owner;
SELECT kernel.register_function('graph.system_orgs(boolean, boolean, text, int)', 'read', NULL, 'global', true);
