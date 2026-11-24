-- generated from the manifest by python -m db.lib.vetted render: edit the manifest, not this file
GRANT pipeshub_vetted_graph TO pipeshub_owner WITH SET TRUE;
GRANT USAGE ON SCHEMA graph TO pipeshub_vetted_graph;           -- the body names objects of its own schema
GRANT CREATE ON SCHEMA graph TO pipeshub_vetted_graph;
SET LOCAL ROLE pipeshub_vetted_graph;
CREATE FUNCTION graph.ticket_sites(p_owner text, p_connector_names text[], p_limit int)
RETURNS TABLE (site_url text) LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, graph, pg_temp
SET standard_conforming_strings = on
SET plan_cache_mode = force_custom_plan
AS $fn$
DECLARE v_org text := NULLIF(current_setting('app.org_id', true), '');
BEGIN
  IF p_owner IS NULL THEN RAISE EXCEPTION 'ticket_sites: p_owner is required' USING ERRCODE = '22023'; END IF;
  IF length(p_owner) NOT BETWEEN 1 AND 200 THEN RAISE EXCEPTION 'ticket_sites: p_owner length must be 1..200' USING ERRCODE = '22023'; END IF;
  IF p_connector_names IS NULL THEN RAISE EXCEPTION 'ticket_sites: p_connector_names is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_connector_names) > 100 THEN RAISE EXCEPTION 'ticket_sites: p_connector_names takes at most 100 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_connector_names) e WHERE e IS NULL) THEN RAISE EXCEPTION 'ticket_sites: p_connector_names has a NULL item' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_connector_names) e WHERE e IS NOT NULL AND length(e) NOT BETWEEN 1 AND 200) THEN RAISE EXCEPTION 'ticket_sites: p_connector_names has an item of the wrong length' USING ERRCODE = '22023'; END IF;
  IF p_limit IS NOT NULL AND p_limit NOT BETWEEN 0 AND 1000000 THEN RAISE EXCEPTION 'ticket_sites: p_limit must be 0..1000000' USING ERRCODE = '22023'; END IF;
  IF v_org IS NULL THEN RETURN; END IF;       -- no org (or someone else's): no rows, the same answer
  RETURN QUERY WITH RECURSIVE s(c, h, site) AS (
      SELECT k.c, f.h, f.site FROM (SELECT DISTINCT c FROM unnest(p_connector_names) c) k
        CROSS JOIN LATERAL (SELECT md5((regexp_match(n.doc->>'webUrl', '^(.*)[/]browse[/]'))[1]) AS h, (regexp_match(n.doc->>'webUrl', '^(.*)[/]browse[/]'))[1] AS site
          FROM graph.nodes n WHERE n.org_id = v_org AND n.coll = 'records' AND n.doc->>'recordType' = 'TICKET' AND n.doc->>'webUrl' LIKE '%/browse/%'
            AND coalesce(n.doc->>'isDeleted', '') <> 'true' AND n.doc->>'connectorName' = k.c AND n.doc->>'orgId' = p_owner
          ORDER BY n.doc->>'connectorName', 1 LIMIT 1) f
      UNION ALL
      SELECT s.c, f.h, f.site FROM s
        CROSS JOIN LATERAL (SELECT md5((regexp_match(n.doc->>'webUrl', '^(.*)[/]browse[/]'))[1]) AS h, (regexp_match(n.doc->>'webUrl', '^(.*)[/]browse[/]'))[1] AS site
          FROM graph.nodes n WHERE n.org_id = v_org AND n.coll = 'records' AND n.doc->>'recordType' = 'TICKET' AND n.doc->>'webUrl' LIKE '%/browse/%'
            AND coalesce(n.doc->>'isDeleted', '') <> 'true' AND n.doc->>'connectorName' = s.c AND n.doc->>'orgId' = p_owner
            AND md5((regexp_match(n.doc->>'webUrl', '^(.*)[/]browse[/]'))[1]) > s.h
          ORDER BY n.doc->>'connectorName', 1 LIMIT 1) f)
    SELECT DISTINCT s.site FROM s WHERE s.site <> '' ORDER BY s.site LIMIT p_limit;
END $fn$;
REVOKE ALL ON FUNCTION graph.ticket_sites(text, text[], int) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION graph.ticket_sites(text, text[], int) TO pipeshub_connectors;
GRANT EXECUTE ON FUNCTION graph.ticket_sites(text, text[], int) TO pipeshub_indexing;
GRANT EXECUTE ON FUNCTION graph.ticket_sites(text, text[], int) TO pipeshub_query;
SET LOCAL ROLE pipeshub_owner;
REVOKE CREATE ON SCHEMA graph FROM pipeshub_vetted_graph;
REVOKE pipeshub_vetted_graph FROM pipeshub_owner;
SELECT kernel.register_function('graph.ticket_sites(text, text[], int)', 'read', NULL, 'org', true);
