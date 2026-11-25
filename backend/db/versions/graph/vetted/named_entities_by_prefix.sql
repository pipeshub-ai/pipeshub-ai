-- generated from the manifest by python -m db.lib.vetted render: edit the manifest, not this file
GRANT pipeshub_vetted_graph TO pipeshub_owner WITH SET TRUE;
GRANT USAGE ON SCHEMA graph TO pipeshub_vetted_graph;           -- the body names objects of its own schema
GRANT CREATE ON SCHEMA graph TO pipeshub_vetted_graph;
SET LOCAL ROLE pipeshub_vetted_graph;
CREATE FUNCTION graph.named_entities_by_prefix(p_owner text, p_kinds text[], p_keys text[], p_limit int DEFAULT 21)
RETURNS TABLE (kind text, key text, node_id text, doc jsonb) LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, graph, pg_temp
SET standard_conforming_strings = on
SET plan_cache_mode = force_custom_plan
AS $fn$
DECLARE v_org text := NULLIF(current_setting('app.org_id', true), '');
BEGIN
  IF p_owner IS NULL THEN RAISE EXCEPTION 'named_entities_by_prefix: p_owner is required' USING ERRCODE = '22023'; END IF;
  IF length(p_owner) NOT BETWEEN 1 AND 200 THEN RAISE EXCEPTION 'named_entities_by_prefix: p_owner length must be 1..200' USING ERRCODE = '22023'; END IF;
  IF p_kinds IS NULL THEN RAISE EXCEPTION 'named_entities_by_prefix: p_kinds is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_kinds) > 1000 THEN RAISE EXCEPTION 'named_entities_by_prefix: p_kinds takes at most 1000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_kinds) e WHERE e IS NULL) THEN RAISE EXCEPTION 'named_entities_by_prefix: p_kinds has a NULL item' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_kinds) e WHERE e IS NOT NULL AND length(e) NOT BETWEEN 1 AND 200) THEN RAISE EXCEPTION 'named_entities_by_prefix: p_kinds has an item of the wrong length' USING ERRCODE = '22023'; END IF;
  IF p_keys IS NULL THEN RAISE EXCEPTION 'named_entities_by_prefix: p_keys is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_keys) > 1000 THEN RAISE EXCEPTION 'named_entities_by_prefix: p_keys takes at most 1000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_keys) e WHERE e IS NULL) THEN RAISE EXCEPTION 'named_entities_by_prefix: p_keys has a NULL item' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_keys) e WHERE e IS NOT NULL AND length(e) NOT BETWEEN 1 AND 1000) THEN RAISE EXCEPTION 'named_entities_by_prefix: p_keys has an item of the wrong length' USING ERRCODE = '22023'; END IF;
  IF p_limit IS NULL THEN RAISE EXCEPTION 'named_entities_by_prefix: p_limit is required' USING ERRCODE = '22023'; END IF;
  IF p_limit NOT BETWEEN 1 AND 1000 THEN RAISE EXCEPTION 'named_entities_by_prefix: p_limit must be 1..1000' USING ERRCODE = '22023'; END IF;
  IF v_org IS NULL THEN RETURN; END IF;       -- no org (or someone else's): no rows, the same answer
  IF cardinality(p_kinds) <> cardinality(p_keys) THEN RAISE EXCEPTION 'p_kinds and p_keys differ in length' USING ERRCODE = '22023'; END IF;
  -- a name begins with the key and a blank exactly when it is in [key + blank, key + "!"): the blank is the byte before "!"
  RETURN QUERY SELECT p.kind, p.key, n.id, n.doc FROM unnest(p_kinds, p_keys) AS p(kind, key)
    CROSS JOIN LATERAL (SELECT x.id, x.doc FROM graph.nodes x WHERE x.org_id = v_org AND x.coll = 'namedEntities' AND x.doc->>'orgId' = p_owner AND x.doc->>'kind' = p.kind
        AND x.doc->>'normKey' ~>=~ (p.key || ' ') AND x.doc->>'normKey' ~<~ (p.key || '!')
        ORDER BY x.doc->>'normKey', x.id LIMIT p_limit) n
    ORDER BY p.key, n.doc->>'normKey', n.id LIMIT 100000;
END $fn$;
REVOKE ALL ON FUNCTION graph.named_entities_by_prefix(text, text[], text[], int) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION graph.named_entities_by_prefix(text, text[], text[], int) TO pipeshub_connectors;
GRANT EXECUTE ON FUNCTION graph.named_entities_by_prefix(text, text[], text[], int) TO pipeshub_indexing;
GRANT EXECUTE ON FUNCTION graph.named_entities_by_prefix(text, text[], text[], int) TO pipeshub_query;
SET LOCAL ROLE pipeshub_owner;
REVOKE CREATE ON SCHEMA graph FROM pipeshub_vetted_graph;
REVOKE pipeshub_vetted_graph FROM pipeshub_owner;
SELECT kernel.register_function('graph.named_entities_by_prefix(text, text[], text[], int)', 'read', NULL, 'org', true);
