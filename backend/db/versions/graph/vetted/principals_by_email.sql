-- generated from the manifest by python -m db.lib.vetted render: edit the manifest, not this file
GRANT pipeshub_vetted_graph TO pipeshub_owner WITH SET TRUE;
GRANT USAGE ON SCHEMA graph TO pipeshub_vetted_graph;           -- the body names objects of its own schema
GRANT CREATE ON SCHEMA graph TO pipeshub_vetted_graph;
SET LOCAL ROLE pipeshub_vetted_graph;
CREATE FUNCTION graph.principals_by_email(p_colls text[], p_emails text[])
RETURNS TABLE (coll text, node_id text, doc jsonb) LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, graph, pg_temp
SET standard_conforming_strings = on
SET plan_cache_mode = force_custom_plan
AS $fn$
DECLARE v_org text := NULLIF(current_setting('app.org_id', true), '');
BEGIN
  IF p_colls IS NULL THEN RAISE EXCEPTION 'principals_by_email: p_colls is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_colls) > 3 THEN RAISE EXCEPTION 'principals_by_email: p_colls takes at most 3 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_colls) e WHERE e IS NULL) THEN RAISE EXCEPTION 'principals_by_email: p_colls has a NULL item' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_colls) e WHERE e IS NOT NULL AND e NOT IN ('users', 'groups', 'person')) THEN RAISE EXCEPTION 'principals_by_email: p_colls has an item that is not one of the allowed values' USING ERRCODE = '22023'; END IF;
  IF p_emails IS NULL THEN RAISE EXCEPTION 'principals_by_email: p_emails is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_emails) > 1000 THEN RAISE EXCEPTION 'principals_by_email: p_emails takes at most 1000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_emails) e WHERE e IS NULL) THEN RAISE EXCEPTION 'principals_by_email: p_emails has a NULL item' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_emails) e WHERE e IS NOT NULL AND length(e) NOT BETWEEN 1 AND 1000) THEN RAISE EXCEPTION 'principals_by_email: p_emails has an item of the wrong length' USING ERRCODE = '22023'; END IF;
  IF v_org IS NULL THEN RETURN; END IF;       -- no org (or someone else's): no rows, the same answer
  -- one branch for each collection, so that the partial index of the collection matches
  RETURN QUERY (SELECT 'users'::text, n.id, n.doc FROM graph.nodes n WHERE 'users' = ANY(p_colls) AND n.org_id = v_org AND n.coll = 'users'
      AND lower(n.doc->>'email') IN (SELECT lower(e) FROM unnest(p_emails) e) LIMIT 5000)
    UNION ALL (SELECT 'groups'::text, n.id, n.doc FROM graph.nodes n WHERE 'groups' = ANY(p_colls) AND n.org_id = v_org AND n.coll = 'groups'
      AND lower(n.doc->>'email') IN (SELECT lower(e) FROM unnest(p_emails) e) LIMIT 5000)
    UNION ALL (SELECT 'person'::text, n.id, n.doc FROM graph.nodes n WHERE 'person' = ANY(p_colls) AND n.org_id = v_org AND n.coll = 'person'
      AND lower(n.doc->>'email') IN (SELECT lower(e) FROM unnest(p_emails) e) LIMIT 5000);
END $fn$;
REVOKE ALL ON FUNCTION graph.principals_by_email(text[], text[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION graph.principals_by_email(text[], text[]) TO pipeshub_connectors;
GRANT EXECUTE ON FUNCTION graph.principals_by_email(text[], text[]) TO pipeshub_indexing;
GRANT EXECUTE ON FUNCTION graph.principals_by_email(text[], text[]) TO pipeshub_query;
SET LOCAL ROLE pipeshub_owner;
REVOKE CREATE ON SCHEMA graph FROM pipeshub_vetted_graph;
REVOKE pipeshub_vetted_graph FROM pipeshub_owner;
SELECT kernel.register_function('graph.principals_by_email(text[], text[])', 'read', NULL, 'org', true);
