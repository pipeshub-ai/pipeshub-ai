-- generated from the manifest by python -m db.lib.vetted render: edit the manifest, not this file
GRANT pipeshub_vetted_graph TO pipeshub_owner WITH SET TRUE;
GRANT USAGE ON SCHEMA graph TO pipeshub_vetted_graph;           -- the body names objects of its own schema
GRANT CREATE ON SCHEMA graph TO pipeshub_vetted_graph;
SET LOCAL ROLE pipeshub_vetted_graph;
CREATE FUNCTION graph.value_mentions_by_records(p_record_ids text[])
RETURNS TABLE (node_id text, doc jsonb) LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, graph, pg_temp
SET standard_conforming_strings = on
SET plan_cache_mode = force_custom_plan
AS $fn$
DECLARE v_org text := NULLIF(current_setting('app.org_id', true), '');
BEGIN
  IF p_record_ids IS NULL THEN RAISE EXCEPTION 'value_mentions_by_records: p_record_ids is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_record_ids) > 5000 THEN RAISE EXCEPTION 'value_mentions_by_records: p_record_ids takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_record_ids) e WHERE e IS NULL) THEN RAISE EXCEPTION 'value_mentions_by_records: p_record_ids has a NULL item' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_record_ids) e WHERE e IS NOT NULL AND length(e) NOT BETWEEN 1 AND 200) THEN RAISE EXCEPTION 'value_mentions_by_records: p_record_ids has an item of the wrong length' USING ERRCODE = '22023'; END IF;
  IF v_org IS NULL THEN RETURN; END IF;       -- no org (or someone else's): no rows, the same answer
  -- one index probe for each record (LATERAL), not a join with the list: from about 10 ids the planner joins them with a scan of the org's mentions (800,000 mentions: 64 to 171 ms,
  -- growing with the org), and a comparison with ANY(list) tests each row against every record (4,000 records: minutes). The rows come with their documents in one query and in
  -- no order (a second fetch of the ids planned as a nested loop over every row when the statistics were stale: hours); a caller orders them
  RETURN QUERY SELECT n.id, n.doc FROM (SELECT DISTINCT r.rid FROM unnest(p_record_ids) AS r(rid)) q
    CROSS JOIN LATERAL (SELECT x.id, x.doc FROM graph.nodes x WHERE x.org_id = v_org AND x.coll = 'valueMentions' AND x.doc->>'recordId' = q.rid LIMIT 100001) n LIMIT 100001;
END $fn$;
REVOKE ALL ON FUNCTION graph.value_mentions_by_records(text[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION graph.value_mentions_by_records(text[]) TO pipeshub_connectors;
GRANT EXECUTE ON FUNCTION graph.value_mentions_by_records(text[]) TO pipeshub_indexing;
GRANT EXECUTE ON FUNCTION graph.value_mentions_by_records(text[]) TO pipeshub_query;
SET LOCAL ROLE pipeshub_owner;
REVOKE CREATE ON SCHEMA graph FROM pipeshub_vetted_graph;
REVOKE pipeshub_vetted_graph FROM pipeshub_owner;
SELECT kernel.register_function('graph.value_mentions_by_records(text[])', 'read', NULL, 'org', true);
