-- generated from the manifest by python -m db.lib.vetted render: edit the manifest, not this file
GRANT pipeshub_vetted_graph TO pipeshub_owner WITH SET TRUE;
GRANT USAGE ON SCHEMA graph TO pipeshub_vetted_graph;           -- the body names objects of its own schema
GRANT CREATE ON SCHEMA graph TO pipeshub_vetted_graph;
SET LOCAL ROLE pipeshub_vetted_graph;
CREATE FUNCTION graph.records_by_revision(p_virtual_record_ids text[], p_revs text[])
RETURNS TABLE (node_id text, doc jsonb) LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, graph, pg_temp
SET standard_conforming_strings = on
SET plan_cache_mode = force_custom_plan
AS $fn$
DECLARE v_org text := NULLIF(current_setting('app.org_id', true), '');
BEGIN
  IF p_virtual_record_ids IS NULL THEN RAISE EXCEPTION 'records_by_revision: p_virtual_record_ids is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_virtual_record_ids) > 1000 THEN RAISE EXCEPTION 'records_by_revision: p_virtual_record_ids takes at most 1000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_virtual_record_ids) e WHERE e IS NULL) THEN RAISE EXCEPTION 'records_by_revision: p_virtual_record_ids has a NULL item' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_virtual_record_ids) e WHERE e IS NOT NULL AND length(e) NOT BETWEEN 1 AND 200) THEN RAISE EXCEPTION 'records_by_revision: p_virtual_record_ids has an item of the wrong length' USING ERRCODE = '22023'; END IF;
  IF p_revs IS NULL THEN RAISE EXCEPTION 'records_by_revision: p_revs is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_revs) > 1000 THEN RAISE EXCEPTION 'records_by_revision: p_revs takes at most 1000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_revs) e WHERE e IS NULL) THEN RAISE EXCEPTION 'records_by_revision: p_revs has a NULL item' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_revs) e WHERE e IS NOT NULL AND length(e) NOT BETWEEN 1 AND 200) THEN RAISE EXCEPTION 'records_by_revision: p_revs has an item of the wrong length' USING ERRCODE = '22023'; END IF;
  IF v_org IS NULL THEN RETURN; END IF;       -- no org (or someone else's): no rows, the same answer
  IF cardinality(p_virtual_record_ids) <> cardinality(p_revs) THEN RAISE EXCEPTION 'p_virtual_record_ids and p_revs differ in length' USING ERRCODE = '22023'; END IF;
  RETURN QUERY SELECT n.id, n.doc FROM (SELECT DISTINCT q.v, q.r FROM unnest(p_virtual_record_ids, p_revs) AS q(v, r)) p
    JOIN graph.nodes n ON n.org_id = v_org AND n.coll = 'records' AND n.doc->>'virtualRecordId' = p.v AND n.doc->>'contentRev' = p.r LIMIT 100000;
END $fn$;
REVOKE ALL ON FUNCTION graph.records_by_revision(text[], text[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION graph.records_by_revision(text[], text[]) TO pipeshub_connectors;
GRANT EXECUTE ON FUNCTION graph.records_by_revision(text[], text[]) TO pipeshub_indexing;
GRANT EXECUTE ON FUNCTION graph.records_by_revision(text[], text[]) TO pipeshub_query;
SET LOCAL ROLE pipeshub_owner;
REVOKE CREATE ON SCHEMA graph FROM pipeshub_vetted_graph;
REVOKE pipeshub_vetted_graph FROM pipeshub_owner;
SELECT kernel.register_function('graph.records_by_revision(text[], text[])', 'read', NULL, 'org', true);
