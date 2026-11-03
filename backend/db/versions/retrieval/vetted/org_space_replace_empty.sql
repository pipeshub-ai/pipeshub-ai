-- generated from the manifest by python -m db.lib.vetted render: edit the manifest, not this file
GRANT pipeshub_vetted_retrieval TO pipeshub_owner WITH SET TRUE;
GRANT USAGE ON SCHEMA retrieval TO pipeshub_vetted_retrieval;           -- the body names objects of its own schema
GRANT CREATE ON SCHEMA retrieval TO pipeshub_vetted_retrieval;
SET LOCAL ROLE pipeshub_vetted_retrieval;
CREATE FUNCTION retrieval.org_space_replace_empty(p_model text, p_dim int, p_metric text)
RETURNS TABLE (replaced boolean) LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, retrieval, pg_temp
SET standard_conforming_strings = on
AS $fn$
DECLARE v_org text := NULLIF(current_setting('app.org_id', true), '');
BEGIN
  IF p_model IS NULL THEN RAISE EXCEPTION 'org_space_replace_empty: p_model is required' USING ERRCODE = '22023'; END IF;
  IF length(p_model) NOT BETWEEN 1 AND 200 THEN RAISE EXCEPTION 'org_space_replace_empty: p_model length must be 1..200' USING ERRCODE = '22023'; END IF;
  IF p_dim IS NULL THEN RAISE EXCEPTION 'org_space_replace_empty: p_dim is required' USING ERRCODE = '22023'; END IF;
  IF p_dim NOT BETWEEN 1 AND 3840 THEN RAISE EXCEPTION 'org_space_replace_empty: p_dim must be 1..3840' USING ERRCODE = '22023'; END IF;
  IF p_metric IS NULL THEN RAISE EXCEPTION 'org_space_replace_empty: p_metric is required' USING ERRCODE = '22023'; END IF;
  IF p_metric NOT IN ('cosine', 'dot', 'l2') THEN RAISE EXCEPTION 'org_space_replace_empty: p_metric is not one of the allowed values' USING ERRCODE = '22023'; END IF;
  IF v_org IS NULL THEN RETURN; END IF;       -- no org (or someone else's): no rows, the same answer
  DECLARE v_sp smallint;
  BEGIN
    IF EXISTS (SELECT 1 FROM retrieval.points p WHERE p.org_id = v_org LIMIT 1) THEN
      RETURN QUERY SELECT false;
      RETURN;
    END IF;
    SELECT s.space_id INTO v_sp FROM retrieval.embedding_spaces s WHERE s.model = p_model AND s.dim = p_dim AND s.metric = p_metric;
    IF v_sp IS NULL THEN          -- select first: an insert that hits the unique key still spends an identity value, and there are 32,767
      BEGIN
        INSERT INTO retrieval.embedding_spaces (model, dim, metric) VALUES (p_model, p_dim, p_metric);
      EXCEPTION WHEN unique_violation THEN NULL;       -- a concurrent writer of the same model added the descriptor first
      END;
      SELECT s.space_id INTO v_sp FROM retrieval.embedding_spaces s WHERE s.model = p_model AND s.dim = p_dim AND s.metric = p_metric;
    END IF;
    BEGIN
      UPDATE retrieval.org_spaces o SET role = 'retiring', dim = 0, named_by = NULL WHERE o.org_id = v_org AND o.role = 'active' AND o.space_id <> v_sp;
      IF EXISTS (SELECT 1 FROM retrieval.org_spaces o WHERE o.org_id = v_org AND o.space_id = v_sp) THEN
        UPDATE retrieval.org_spaces o SET role = 'active', dim = p_dim, named_by = NULL WHERE o.org_id = v_org AND o.space_id = v_sp;
      ELSE
        INSERT INTO retrieval.org_spaces (org_id, space_id, dim, role) VALUES (v_org, v_sp, p_dim, 'active');
      END IF;
    EXCEPTION WHEN foreign_key_violation OR unique_violation THEN
      RETURN QUERY SELECT false;
      RETURN;
    END;
    RETURN QUERY SELECT true;
  END;
END $fn$;
REVOKE ALL ON FUNCTION retrieval.org_space_replace_empty(text, int, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION retrieval.org_space_replace_empty(text, int, text) TO pipeshub_indexing;
SET LOCAL ROLE pipeshub_owner;
REVOKE CREATE ON SCHEMA retrieval FROM pipeshub_vetted_retrieval;
REVOKE pipeshub_vetted_retrieval FROM pipeshub_owner;
SELECT kernel.register_function('retrieval.org_space_replace_empty(text, int, text)', 'write', 'pipeshub_indexing', 'org', false);
