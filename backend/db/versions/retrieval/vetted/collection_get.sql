-- generated from the manifest by python -m db.lib.vetted render: edit the manifest, not this file
GRANT pipeshub_vetted_retrieval TO pipeshub_owner WITH SET TRUE;
GRANT USAGE ON SCHEMA retrieval TO pipeshub_vetted_retrieval;           -- the body names objects of its own schema
GRANT CREATE ON SCHEMA retrieval TO pipeshub_vetted_retrieval;
SET LOCAL ROLE pipeshub_vetted_retrieval;
CREATE FUNCTION retrieval.collection_get(p_name text)
RETURNS TABLE (collection_id smallint, sparse_enabled boolean, sparse_idf boolean, lexical_version text, status text) LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, retrieval, pg_temp
SET standard_conforming_strings = on
AS $fn$
BEGIN
  IF p_name IS NULL THEN RAISE EXCEPTION 'collection_get: p_name is required' USING ERRCODE = '22023'; END IF;
  IF length(p_name) NOT BETWEEN 1 AND 200 THEN RAISE EXCEPTION 'collection_get: p_name length must be 1..200' USING ERRCODE = '22023'; END IF;
  RETURN QUERY SELECT c.collection_id, c.sparse_enabled, c.sparse_idf, c.lexical_version, c.status
    FROM retrieval.collections c WHERE c.name = p_name AND c.status <> 'deleting' LIMIT 1;
END $fn$;
REVOKE ALL ON FUNCTION retrieval.collection_get(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION retrieval.collection_get(text) TO pipeshub_app;
SET LOCAL ROLE pipeshub_owner;
REVOKE CREATE ON SCHEMA retrieval FROM pipeshub_vetted_retrieval;
REVOKE pipeshub_vetted_retrieval FROM pipeshub_owner;
SELECT kernel.register_function('retrieval.collection_get(text)', 'read', NULL, 'global', false);
