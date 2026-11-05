-- generated from the manifest by python -m db.lib.vetted render: edit the manifest, not this file
GRANT pipeshub_vetted_retrieval TO pipeshub_owner WITH SET TRUE;
GRANT USAGE ON SCHEMA retrieval TO pipeshub_vetted_retrieval;           -- the body names objects of its own schema
GRANT CREATE ON SCHEMA retrieval TO pipeshub_vetted_retrieval;
SET LOCAL ROLE pipeshub_vetted_retrieval;
CREATE FUNCTION retrieval.collection_create(p_name text, p_sparse_enabled boolean, p_sparse_idf boolean, p_lexical_version text)
RETURNS TABLE (new_id smallint) LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, retrieval, pg_temp
SET standard_conforming_strings = on
AS $fn$
BEGIN
  IF p_name IS NULL THEN RAISE EXCEPTION 'collection_create: p_name is required' USING ERRCODE = '22023'; END IF;
  IF length(p_name) NOT BETWEEN 1 AND 200 THEN RAISE EXCEPTION 'collection_create: p_name length must be 1..200' USING ERRCODE = '22023'; END IF;
  IF p_sparse_enabled IS NULL THEN RAISE EXCEPTION 'collection_create: p_sparse_enabled is required' USING ERRCODE = '22023'; END IF;
  IF p_sparse_idf IS NULL THEN RAISE EXCEPTION 'collection_create: p_sparse_idf is required' USING ERRCODE = '22023'; END IF;
  IF p_lexical_version IS NULL THEN RAISE EXCEPTION 'collection_create: p_lexical_version is required' USING ERRCODE = '22023'; END IF;
  IF length(p_lexical_version) NOT BETWEEN 1 AND 200 THEN RAISE EXCEPTION 'collection_create: p_lexical_version length must be 1..200' USING ERRCODE = '22023'; END IF;
  RETURN QUERY WITH ins AS (
    INSERT INTO retrieval.collections (name, sparse_enabled, sparse_idf, lexical_version)
    VALUES (p_name, p_sparse_enabled, p_sparse_idf, p_lexical_version) RETURNING collections.collection_id)
    SELECT ins.collection_id FROM ins LIMIT 1;
END $fn$;
REVOKE ALL ON FUNCTION retrieval.collection_create(text, boolean, boolean, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION retrieval.collection_create(text, boolean, boolean, text) TO pipeshub_jobs;
SET LOCAL ROLE pipeshub_owner;
REVOKE CREATE ON SCHEMA retrieval FROM pipeshub_vetted_retrieval;
REVOKE pipeshub_vetted_retrieval FROM pipeshub_owner;
SELECT kernel.register_function('retrieval.collection_create(text, boolean, boolean, text)', 'jobs', NULL, 'global', false);
