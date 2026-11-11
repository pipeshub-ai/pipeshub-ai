-- generated from the manifest by python -m db.lib.vetted render: edit the manifest, not this file
GRANT pipeshub_vetted_graph TO pipeshub_owner WITH SET TRUE;
GRANT USAGE ON SCHEMA graph TO pipeshub_vetted_graph;           -- the body names objects of its own schema
GRANT CREATE ON SCHEMA graph TO pipeshub_vetted_graph;
SET LOCAL ROLE pipeshub_vetted_graph;
CREATE FUNCTION graph.people_by_name(p_name text, p_limit int DEFAULT 10)
RETURNS TABLE (node_id text, doc jsonb) LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, graph, pg_temp
SET standard_conforming_strings = on
SET plan_cache_mode = force_custom_plan
AS $fn$
DECLARE v_org text := NULLIF(current_setting('app.org_id', true), '');
BEGIN
  IF p_name IS NULL THEN RAISE EXCEPTION 'people_by_name: p_name is required' USING ERRCODE = '22023'; END IF;
  IF length(p_name) NOT BETWEEN 1 AND 400 THEN RAISE EXCEPTION 'people_by_name: p_name length must be 1..400' USING ERRCODE = '22023'; END IF;
  IF p_limit IS NULL THEN RAISE EXCEPTION 'people_by_name: p_limit is required' USING ERRCODE = '22023'; END IF;
  IF p_limit NOT BETWEEN 1 AND 1000 THEN RAISE EXCEPTION 'people_by_name: p_limit must be 1..1000' USING ERRCODE = '22023'; END IF;
  IF v_org IS NULL THEN RETURN; END IF;       -- no org (or someone else's): no rows, the same answer
  RETURN QUERY WITH u AS (SELECT n.id, n.doc, btrim(regexp_replace(lower(coalesce(n.doc->>'fullName', '')), ' +', ' ', 'g'), ' ') AS name,
        btrim(regexp_replace(lower(coalesce(n.doc->>'firstName', '') || ' ' || coalesce(n.doc->>'lastName', '')), ' +', ' ', 'g'), ' ') AS parts
      FROM graph.nodes n WHERE n.org_id = v_org AND n.coll = 'person' AND (
        btrim(regexp_replace(lower(coalesce(n.doc->>'fullName', '')), ' +', ' ', 'g'), ' ') = p_name
        OR btrim(regexp_replace(lower(coalesce(n.doc->>'firstName', '') || ' ' || coalesce(n.doc->>'lastName', '')), ' +', ' ', 'g'), ' ') = p_name
        OR (length(p_name) >= 3 AND btrim(regexp_replace(lower(coalesce(n.doc->>'fullName', '')), ' +', ' ', 'g'), ' ')
            LIKE '%' || replace(replace(replace(p_name, '\', '\\'), '%', '\%'), '_', '\_') || '%'))),
    hits AS (SELECT u.id, u.doc, u.name, (u.name = p_name OR u.parts = p_name) AS exact FROM u
      WHERE u.name = p_name OR u.parts = p_name
        OR (length(p_name) >= 3 AND (starts_with(u.name, p_name) OR EXISTS (SELECT 1 FROM unnest(string_to_array(u.name, ' ')) w WHERE starts_with(w, p_name)))))
    SELECT h.id, h.doc FROM hits h WHERE h.exact OR NOT EXISTS (SELECT 1 FROM hits x WHERE x.exact)
    ORDER BY h.name COLLATE ucs_basic, h.id COLLATE ucs_basic LIMIT p_limit;
END $fn$;
REVOKE ALL ON FUNCTION graph.people_by_name(text, int) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION graph.people_by_name(text, int) TO pipeshub_connectors;
GRANT EXECUTE ON FUNCTION graph.people_by_name(text, int) TO pipeshub_indexing;
GRANT EXECUTE ON FUNCTION graph.people_by_name(text, int) TO pipeshub_query;
SET LOCAL ROLE pipeshub_owner;
REVOKE CREATE ON SCHEMA graph FROM pipeshub_vetted_graph;
REVOKE pipeshub_vetted_graph FROM pipeshub_owner;
SELECT kernel.register_function('graph.people_by_name(text, int)', 'read', NULL, 'org', true);
