-- generated from the manifest by python -m db.lib.vetted render: edit the manifest, not this file
GRANT pipeshub_vetted_graph TO pipeshub_owner WITH SET TRUE;
GRANT USAGE ON SCHEMA graph TO pipeshub_vetted_graph;           -- the body names objects of its own schema
GRANT CREATE ON SCHEMA graph TO pipeshub_vetted_graph;
SET LOCAL ROLE pipeshub_vetted_graph;
CREATE FUNCTION graph.records_by_weburl(p_urls text[])
RETURNS TABLE (node_id text, doc jsonb) LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, graph, pg_temp
SET standard_conforming_strings = on
SET plan_cache_mode = force_custom_plan
AS $fn$
DECLARE v_org text := NULLIF(current_setting('app.org_id', true), '');
BEGIN
  IF p_urls IS NULL THEN RAISE EXCEPTION 'records_by_weburl: p_urls is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_urls) > 1000 THEN RAISE EXCEPTION 'records_by_weburl: p_urls takes at most 1000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_urls) e WHERE e IS NULL) THEN RAISE EXCEPTION 'records_by_weburl: p_urls has a NULL item' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_urls) e WHERE e IS NOT NULL AND length(e) NOT BETWEEN 0 AND 100000) THEN RAISE EXCEPTION 'records_by_weburl: p_urls has an item of the wrong length' USING ERRCODE = '22023'; END IF;
  IF v_org IS NULL THEN RETURN; END IF;       -- no org (or someone else's): no rows, the same answer
  RETURN QUERY SELECT m.id, m.doc FROM (SELECT DISTINCT u FROM unnest(p_urls) u) k
    CROSS JOIN LATERAL (SELECT n.id, n.doc FROM graph.nodes n
      WHERE n.org_id = v_org AND n.coll = 'records' AND md5(n.doc->>'webUrl') = md5(k.u) AND n.doc->>'webUrl' = k.u LIMIT 10000) m;
END $fn$;
REVOKE ALL ON FUNCTION graph.records_by_weburl(text[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION graph.records_by_weburl(text[]) TO pipeshub_connectors;
GRANT EXECUTE ON FUNCTION graph.records_by_weburl(text[]) TO pipeshub_indexing;
GRANT EXECUTE ON FUNCTION graph.records_by_weburl(text[]) TO pipeshub_query;
SET LOCAL ROLE pipeshub_owner;
REVOKE CREATE ON SCHEMA graph FROM pipeshub_vetted_graph;
REVOKE pipeshub_vetted_graph FROM pipeshub_owner;
SELECT kernel.register_function('graph.records_by_weburl(text[])', 'read', NULL, 'org', true);
