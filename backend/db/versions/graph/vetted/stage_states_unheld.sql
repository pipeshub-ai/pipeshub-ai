-- generated from the manifest by python -m db.lib.vetted render: edit the manifest, not this file
GRANT pipeshub_vetted_graph TO pipeshub_owner WITH SET TRUE;
GRANT USAGE ON SCHEMA graph TO pipeshub_vetted_graph;           -- the body names objects of its own schema
GRANT CREATE ON SCHEMA graph TO pipeshub_vetted_graph;
SET LOCAL ROLE pipeshub_vetted_graph;
CREATE FUNCTION graph.stage_states_unheld(p_virtual_record_ids text[])
RETURNS TABLE (virtual_record_id text, revision text, updated_at_ms text) LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, graph, pg_temp
SET standard_conforming_strings = on
SET plan_cache_mode = force_custom_plan
AS $fn$
DECLARE v_org text := NULLIF(current_setting('app.org_id', true), '');
BEGIN
  IF p_virtual_record_ids IS NULL THEN RAISE EXCEPTION 'stage_states_unheld: p_virtual_record_ids is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_virtual_record_ids) > 1000 THEN RAISE EXCEPTION 'stage_states_unheld: p_virtual_record_ids takes at most 1000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_virtual_record_ids) e WHERE e IS NULL) THEN RAISE EXCEPTION 'stage_states_unheld: p_virtual_record_ids has a NULL item' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_virtual_record_ids) e WHERE e IS NOT NULL AND length(e) NOT BETWEEN 1 AND 200) THEN RAISE EXCEPTION 'stage_states_unheld: p_virtual_record_ids has an item of the wrong length' USING ERRCODE = '22023'; END IF;
  IF v_org IS NULL THEN RETURN; END IF;       -- no org (or someone else's): no rows, the same answer
  RETURN QUERY SELECT s.doc->>'virtualRecordId', s.doc->>'rev', s.doc->>'updatedAtMs' FROM graph.nodes s
    WHERE s.org_id = v_org AND s.coll = 'stageStates' AND s.doc->>'virtualRecordId' = ANY(p_virtual_record_ids) AND s.doc->>'rev' IS NOT NULL
      AND NOT EXISTS (SELECT 1 FROM graph.nodes r WHERE r.org_id = v_org AND r.coll = 'records' AND r.doc->>'virtualRecordId' = s.doc->>'virtualRecordId' AND r.doc->>'contentRev' = s.doc->>'rev')
    LIMIT 100000;
END $fn$;
REVOKE ALL ON FUNCTION graph.stage_states_unheld(text[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION graph.stage_states_unheld(text[]) TO pipeshub_connectors;
GRANT EXECUTE ON FUNCTION graph.stage_states_unheld(text[]) TO pipeshub_indexing;
GRANT EXECUTE ON FUNCTION graph.stage_states_unheld(text[]) TO pipeshub_query;
SET LOCAL ROLE pipeshub_owner;
REVOKE CREATE ON SCHEMA graph FROM pipeshub_vetted_graph;
REVOKE pipeshub_vetted_graph FROM pipeshub_owner;
SELECT kernel.register_function('graph.stage_states_unheld(text[])', 'read', NULL, 'org', true);
