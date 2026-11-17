-- generated from the manifest by python -m db.lib.vetted render: edit the manifest, not this file
GRANT pipeshub_vetted_graph TO pipeshub_owner WITH SET TRUE;
GRANT USAGE ON SCHEMA graph TO pipeshub_vetted_graph;           -- the body names objects of its own schema
GRANT CREATE ON SCHEMA graph TO pipeshub_vetted_graph;
SET LOCAL ROLE pipeshub_vetted_graph;
CREATE FUNCTION graph.person_by_source_key(p_source_key text)
RETURNS TABLE (node_id text, doc jsonb) LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, graph, pg_temp
SET standard_conforming_strings = on
SET plan_cache_mode = force_custom_plan
AS $fn$
DECLARE v_org text := NULLIF(current_setting('app.org_id', true), '');
BEGIN
  IF p_source_key IS NULL THEN RAISE EXCEPTION 'person_by_source_key: p_source_key is required' USING ERRCODE = '22023'; END IF;
  IF length(p_source_key) NOT BETWEEN 1 AND 400 THEN RAISE EXCEPTION 'person_by_source_key: p_source_key length must be 1..400' USING ERRCODE = '22023'; END IF;
  IF v_org IS NULL THEN RETURN; END IF;       -- no org (or someone else's): no rows, the same answer
  -- the ids by the key's index first, then ordered: an order in the same query can walk the primary key and filter the whole collection
  RETURN QUERY WITH m AS MATERIALIZED (SELECT n.id FROM graph.nodes n WHERE n.org_id = v_org AND n.coll = 'person' AND n.doc->>'sourceKey' = p_source_key)
    SELECT n.id, n.doc FROM graph.nodes n WHERE n.org_id = v_org AND n.coll = 'person' AND n.id = (SELECT min(m.id) FROM m) LIMIT 1;
END $fn$;
REVOKE ALL ON FUNCTION graph.person_by_source_key(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION graph.person_by_source_key(text) TO pipeshub_connectors;
GRANT EXECUTE ON FUNCTION graph.person_by_source_key(text) TO pipeshub_indexing;
GRANT EXECUTE ON FUNCTION graph.person_by_source_key(text) TO pipeshub_query;
SET LOCAL ROLE pipeshub_owner;
REVOKE CREATE ON SCHEMA graph FROM pipeshub_vetted_graph;
REVOKE pipeshub_vetted_graph FROM pipeshub_owner;
SELECT kernel.register_function('graph.person_by_source_key(text)', 'read', NULL, 'org', true);
