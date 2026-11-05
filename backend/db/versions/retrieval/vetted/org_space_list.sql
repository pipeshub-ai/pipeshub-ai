-- generated from the manifest by python -m db.lib.vetted render: edit the manifest, not this file
GRANT pipeshub_vetted_retrieval TO pipeshub_owner WITH SET TRUE;
GRANT USAGE ON SCHEMA retrieval TO pipeshub_vetted_retrieval;           -- the body names objects of its own schema
GRANT CREATE ON SCHEMA retrieval TO pipeshub_vetted_retrieval;
SET LOCAL ROLE pipeshub_vetted_retrieval;
CREATE FUNCTION retrieval.org_space_list()
RETURNS TABLE (space_id smallint, space_role text, model text, dim int, metric text) LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, retrieval, pg_temp
SET standard_conforming_strings = on
AS $fn$
DECLARE v_org text := NULLIF(current_setting('app.org_id', true), '');
BEGIN
  IF v_org IS NULL THEN RETURN; END IF;       -- no org (or someone else's): no rows, the same answer
  RETURN QUERY SELECT o.space_id, o.role, s.model, s.dim, s.metric
    FROM retrieval.org_spaces o JOIN retrieval.embedding_spaces s ON s.space_id = o.space_id
    WHERE o.org_id = v_org ORDER BY o.space_id LIMIT 16;
END $fn$;
REVOKE ALL ON FUNCTION retrieval.org_space_list() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION retrieval.org_space_list() TO pipeshub_app;
SET LOCAL ROLE pipeshub_owner;
REVOKE CREATE ON SCHEMA retrieval FROM pipeshub_vetted_retrieval;
REVOKE pipeshub_vetted_retrieval FROM pipeshub_owner;
SELECT kernel.register_function('retrieval.org_space_list()', 'read', NULL, 'org', false);
