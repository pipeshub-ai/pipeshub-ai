-- generated from the manifest by python -m db.lib.vetted render: edit the manifest, not this file
GRANT pipeshub_vetted_graph TO pipeshub_owner WITH SET TRUE;
GRANT USAGE ON SCHEMA graph TO pipeshub_vetted_graph;           -- the body names objects of its own schema
GRANT CREATE ON SCHEMA graph TO pipeshub_vetted_graph;
SET LOCAL ROLE pipeshub_vetted_graph;
CREATE FUNCTION graph.ref_links(p_doc_ids text[], p_upload text, p_kb text, p_per_doc int)
RETURNS TABLE (doc_id text, kind text, node_id text, origin text, connector_name text, connector_id text) LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, graph, pg_temp
SET standard_conforming_strings = on
SET plan_cache_mode = force_custom_plan
AS $fn$
DECLARE v_org text := NULLIF(current_setting('app.org_id', true), '');
BEGIN
  IF p_doc_ids IS NULL THEN RAISE EXCEPTION 'ref_links: p_doc_ids is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_doc_ids) > 1000 THEN RAISE EXCEPTION 'ref_links: p_doc_ids takes at most 1000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_doc_ids) e WHERE e IS NULL) THEN RAISE EXCEPTION 'ref_links: p_doc_ids has a NULL item' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_doc_ids) e WHERE e IS NOT NULL AND length(e) NOT BETWEEN 1 AND 200) THEN RAISE EXCEPTION 'ref_links: p_doc_ids has an item of the wrong length' USING ERRCODE = '22023'; END IF;
  IF p_upload IS NULL THEN RAISE EXCEPTION 'ref_links: p_upload is required' USING ERRCODE = '22023'; END IF;
  IF length(p_upload) NOT BETWEEN 1 AND 100 THEN RAISE EXCEPTION 'ref_links: p_upload length must be 1..100' USING ERRCODE = '22023'; END IF;
  IF p_kb IS NULL THEN RAISE EXCEPTION 'ref_links: p_kb is required' USING ERRCODE = '22023'; END IF;
  IF length(p_kb) NOT BETWEEN 1 AND 100 THEN RAISE EXCEPTION 'ref_links: p_kb length must be 1..100' USING ERRCODE = '22023'; END IF;
  IF p_per_doc IS NULL THEN RAISE EXCEPTION 'ref_links: p_per_doc is required' USING ERRCODE = '22023'; END IF;
  IF p_per_doc NOT BETWEEN 1 AND 100 THEN RAISE EXCEPTION 'ref_links: p_per_doc must be 1..100' USING ERRCODE = '22023'; END IF;
  IF v_org IS NULL THEN RETURN; END IF;       -- no org (or someone else's): no rows, the same answer
  -- coalesce: the upload test is NULL for a record without an origin or connector name, and a descending order puts NULL first
  RETURN QUERY SELECT d.e, x.k, x.id, x.o, x.cn, x.ci FROM (SELECT DISTINCT e FROM unnest(p_doc_ids) e) d
    CROSS JOIN LATERAL ((SELECT 'externalRecordId'::text AS k, n.id, n.doc->>'origin' AS o, n.doc->>'connectorName' AS cn, n.doc->>'connectorId' AS ci FROM graph.nodes n
        WHERE n.org_id = v_org AND n.coll = 'records' AND n.doc->>'externalRecordId' = d.e
        ORDER BY coalesce(n.doc->>'origin' = p_upload AND n.doc->>'connectorName' = p_kb, false) DESC, n.id COLLATE ucs_basic LIMIT p_per_doc)
      UNION ALL (SELECT 'files.path'::text, f.id, NULL::text, NULL::text, NULL::text FROM graph.nodes f
        WHERE f.org_id = v_org AND f.coll = 'files' AND f.doc->>'path' = 'storage://' || d.e ORDER BY f.id COLLATE ucs_basic LIMIT 1)) x;
END $fn$;
REVOKE ALL ON FUNCTION graph.ref_links(text[], text, text, int) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION graph.ref_links(text[], text, text, int) TO pipeshub_connectors;
GRANT EXECUTE ON FUNCTION graph.ref_links(text[], text, text, int) TO pipeshub_indexing;
GRANT EXECUTE ON FUNCTION graph.ref_links(text[], text, text, int) TO pipeshub_query;
SET LOCAL ROLE pipeshub_owner;
REVOKE CREATE ON SCHEMA graph FROM pipeshub_vetted_graph;
REVOKE pipeshub_vetted_graph FROM pipeshub_owner;
SELECT kernel.register_function('graph.ref_links(text[], text, text, int)', 'read', NULL, 'org', true);
