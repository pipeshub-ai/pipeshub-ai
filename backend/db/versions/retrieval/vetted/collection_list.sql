-- generated from the manifest by python -m db.lib.vetted render: edit the manifest, not this file
GRANT pipeshub_vetted_retrieval TO pipeshub_owner WITH SET TRUE;
GRANT USAGE ON SCHEMA retrieval TO pipeshub_vetted_retrieval;           -- the body names objects of its own schema
GRANT CREATE ON SCHEMA retrieval TO pipeshub_vetted_retrieval;
SET LOCAL ROLE pipeshub_vetted_retrieval;
CREATE FUNCTION retrieval.collection_list(p_after int DEFAULT 0, p_limit int DEFAULT 1000)
RETURNS TABLE (collection_id smallint, name text, sparse_enabled boolean, sparse_idf boolean, lexical_version text, status text) LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, retrieval, pg_temp
SET standard_conforming_strings = on
AS $fn$
BEGIN
  IF p_after IS NULL THEN RAISE EXCEPTION 'collection_list: p_after is required' USING ERRCODE = '22023'; END IF;
  IF p_after NOT BETWEEN 0 AND 32767 THEN RAISE EXCEPTION 'collection_list: p_after must be 0..32767' USING ERRCODE = '22023'; END IF;
  IF p_limit IS NULL THEN RAISE EXCEPTION 'collection_list: p_limit is required' USING ERRCODE = '22023'; END IF;
  IF p_limit NOT BETWEEN 1 AND 1000 THEN RAISE EXCEPTION 'collection_list: p_limit must be 1..1000' USING ERRCODE = '22023'; END IF;
  RETURN QUERY SELECT c.collection_id, c.name, c.sparse_enabled, c.sparse_idf, c.lexical_version, c.status
    FROM retrieval.collections c WHERE c.status <> 'deleting' AND c.collection_id > p_after ORDER BY c.collection_id LIMIT p_limit;
END $fn$;
REVOKE ALL ON FUNCTION retrieval.collection_list(int, int) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION retrieval.collection_list(int, int) TO pipeshub_app;
SET LOCAL ROLE pipeshub_owner;
REVOKE CREATE ON SCHEMA retrieval FROM pipeshub_vetted_retrieval;
REVOKE pipeshub_vetted_retrieval FROM pipeshub_owner;
SELECT kernel.register_function('retrieval.collection_list(int, int)', 'read', NULL, 'global', false);
