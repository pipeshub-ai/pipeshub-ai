-- generated from the manifest by python -m db.lib.vetted render: edit the manifest, not this file
GRANT pipeshub_vetted_graph TO pipeshub_owner WITH SET TRUE;
GRANT USAGE ON SCHEMA graph TO pipeshub_vetted_graph;           -- the body names objects of its own schema
GRANT CREATE ON SCHEMA graph TO pipeshub_vetted_graph;
SET LOCAL ROLE pipeshub_vetted_graph;
CREATE FUNCTION graph.named_entities_by_keys(p_owner text, p_kind text, p_keys text[])
RETURNS TABLE (node_id text, doc jsonb) LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, graph, pg_temp
SET standard_conforming_strings = on
SET plan_cache_mode = force_custom_plan
AS $fn$
DECLARE v_org text := NULLIF(current_setting('app.org_id', true), '');
BEGIN
  IF p_owner IS NULL THEN RAISE EXCEPTION 'named_entities_by_keys: p_owner is required' USING ERRCODE = '22023'; END IF;
  IF length(p_owner) NOT BETWEEN 1 AND 200 THEN RAISE EXCEPTION 'named_entities_by_keys: p_owner length must be 1..200' USING ERRCODE = '22023'; END IF;
  IF p_kind IS NULL THEN RAISE EXCEPTION 'named_entities_by_keys: p_kind is required' USING ERRCODE = '22023'; END IF;
  IF length(p_kind) NOT BETWEEN 1 AND 200 THEN RAISE EXCEPTION 'named_entities_by_keys: p_kind length must be 1..200' USING ERRCODE = '22023'; END IF;
  IF p_keys IS NULL THEN RAISE EXCEPTION 'named_entities_by_keys: p_keys is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_keys) > 1000 THEN RAISE EXCEPTION 'named_entities_by_keys: p_keys takes at most 1000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_keys) e WHERE e IS NULL) THEN RAISE EXCEPTION 'named_entities_by_keys: p_keys has a NULL item' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_keys) e WHERE e IS NOT NULL AND length(e) NOT BETWEEN 1 AND 1000) THEN RAISE EXCEPTION 'named_entities_by_keys: p_keys has an item of the wrong length' USING ERRCODE = '22023'; END IF;
  IF v_org IS NULL THEN RETURN; END IF;       -- no org (or someone else's): no rows, the same answer
  -- two arms, each by its own index: with the OR, the planner scans every entity of the kind (its estimates under row-level security are the defaults), and in the alias arm
  -- the kind is compared as (kind || '') so that the btree index on the kind does not compete with the alias index
  RETURN QUERY WITH m AS MATERIALIZED (
      SELECT n.id FROM graph.nodes n WHERE n.org_id = v_org AND n.coll = 'namedEntities' AND n.doc->>'orgId' = p_owner AND n.doc->>'kind' = p_kind AND n.doc->>'normKey' = ANY(p_keys)
      UNION SELECT n.id FROM graph.nodes n WHERE n.org_id = v_org AND n.coll = 'namedEntities' AND n.doc->>'orgId' = p_owner AND (n.doc->>'kind') || '' = p_kind
        AND n.doc->'normalizedAliases' ?| p_keys)
    SELECT n.id, n.doc FROM graph.nodes n WHERE n.org_id = v_org AND n.coll = 'namedEntities' AND n.id IN (SELECT m.id FROM m) ORDER BY n.id LIMIT 100000;
END $fn$;
REVOKE ALL ON FUNCTION graph.named_entities_by_keys(text, text, text[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION graph.named_entities_by_keys(text, text, text[]) TO pipeshub_connectors;
GRANT EXECUTE ON FUNCTION graph.named_entities_by_keys(text, text, text[]) TO pipeshub_indexing;
GRANT EXECUTE ON FUNCTION graph.named_entities_by_keys(text, text, text[]) TO pipeshub_query;
SET LOCAL ROLE pipeshub_owner;
REVOKE CREATE ON SCHEMA graph FROM pipeshub_vetted_graph;
REVOKE pipeshub_vetted_graph FROM pipeshub_owner;
SELECT kernel.register_function('graph.named_entities_by_keys(text, text, text[])', 'read', NULL, 'org', true);
