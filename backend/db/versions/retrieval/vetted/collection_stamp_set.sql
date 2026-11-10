-- generated from the manifest by python -m db.lib.vetted render: edit the manifest, not this file
GRANT pipeshub_vetted_retrieval TO pipeshub_owner WITH SET TRUE;
GRANT USAGE ON SCHEMA retrieval TO pipeshub_vetted_retrieval;           -- the body names objects of its own schema
GRANT CREATE ON SCHEMA retrieval TO pipeshub_vetted_retrieval;
SET LOCAL ROLE pipeshub_vetted_retrieval;
CREATE FUNCTION retrieval.collection_stamp_set(p_name text, p_stamp text)
RETURNS TABLE (stamp text) LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, retrieval, pg_temp
SET standard_conforming_strings = on
AS $fn$
BEGIN
  IF p_name IS NULL THEN RAISE EXCEPTION 'collection_stamp_set: p_name is required' USING ERRCODE = '22023'; END IF;
  IF length(p_name) NOT BETWEEN 1 AND 200 THEN RAISE EXCEPTION 'collection_stamp_set: p_name length must be 1..200' USING ERRCODE = '22023'; END IF;
  IF p_stamp IS NULL THEN RAISE EXCEPTION 'collection_stamp_set: p_stamp is required' USING ERRCODE = '22023'; END IF;
  IF length(p_stamp) NOT BETWEEN 1 AND 64 THEN RAISE EXCEPTION 'collection_stamp_set: p_stamp length must be 1..64' USING ERRCODE = '22023'; END IF;
  UPDATE retrieval.collections c SET stamp = p_stamp WHERE c.name = p_name AND c.status <> 'deleting' AND c.stamp IS NULL;
  RETURN QUERY SELECT c.stamp FROM retrieval.collections c WHERE c.name = p_name AND c.status <> 'deleting' LIMIT 1;
END $fn$;
REVOKE ALL ON FUNCTION retrieval.collection_stamp_set(text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION retrieval.collection_stamp_set(text, text) TO pipeshub_indexing;
SET LOCAL ROLE pipeshub_owner;
REVOKE CREATE ON SCHEMA retrieval FROM pipeshub_vetted_retrieval;
REVOKE pipeshub_vetted_retrieval FROM pipeshub_owner;
SELECT kernel.register_function('retrieval.collection_stamp_set(text, text)', 'write', 'pipeshub_indexing', 'global', false);
