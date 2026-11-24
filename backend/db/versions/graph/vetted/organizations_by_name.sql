-- generated from the manifest by python -m db.lib.vetted render: edit the manifest, not this file
GRANT pipeshub_vetted_graph TO pipeshub_owner WITH SET TRUE;
GRANT USAGE ON SCHEMA graph TO pipeshub_vetted_graph;           -- the body names objects of its own schema
GRANT CREATE ON SCHEMA graph TO pipeshub_vetted_graph;
SET LOCAL ROLE pipeshub_vetted_graph;
CREATE FUNCTION graph.organizations_by_name(p_parent_id text, p_names text[])
RETURNS TABLE (node_id text, doc jsonb) LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, graph, pg_temp
SET standard_conforming_strings = on
SET plan_cache_mode = force_custom_plan
AS $fn$
DECLARE v_org text := NULLIF(current_setting('app.org_id', true), '');
BEGIN
  IF p_parent_id IS NULL THEN RAISE EXCEPTION 'organizations_by_name: p_parent_id is required' USING ERRCODE = '22023'; END IF;
  IF length(p_parent_id) NOT BETWEEN 1 AND 200 THEN RAISE EXCEPTION 'organizations_by_name: p_parent_id length must be 1..200' USING ERRCODE = '22023'; END IF;
  IF p_names IS NULL THEN RAISE EXCEPTION 'organizations_by_name: p_names is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_names) > 1000 THEN RAISE EXCEPTION 'organizations_by_name: p_names takes at most 1000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_names) e WHERE e IS NULL) THEN RAISE EXCEPTION 'organizations_by_name: p_names has a NULL item' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_names) e WHERE e IS NOT NULL AND length(e) NOT BETWEEN 1 AND 1000) THEN RAISE EXCEPTION 'organizations_by_name: p_names has an item of the wrong length' USING ERRCODE = '22023'; END IF;
  IF v_org IS NULL THEN RETURN; END IF;       -- no org (or someone else's): no rows, the same answer
  RETURN QUERY SELECT n.id, n.doc FROM graph.nodes n
    WHERE n.org_id = v_org AND n.coll = 'organizations' AND n.doc->>'normalizedName' = ANY(p_names)
      AND n.doc->>'parentOrgId' = p_parent_id AND n.doc @> '{"isExternal": true}'
    LIMIT 5000;
END $fn$;
REVOKE ALL ON FUNCTION graph.organizations_by_name(text, text[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION graph.organizations_by_name(text, text[]) TO pipeshub_connectors;
GRANT EXECUTE ON FUNCTION graph.organizations_by_name(text, text[]) TO pipeshub_indexing;
GRANT EXECUTE ON FUNCTION graph.organizations_by_name(text, text[]) TO pipeshub_query;
SET LOCAL ROLE pipeshub_owner;
REVOKE CREATE ON SCHEMA graph FROM pipeshub_vetted_graph;
REVOKE pipeshub_vetted_graph FROM pipeshub_owner;
SELECT kernel.register_function('graph.organizations_by_name(text, text[])', 'read', NULL, 'org', true);
