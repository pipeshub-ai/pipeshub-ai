-- generated from the manifest by python -m db.lib.vetted render: edit the manifest, not this file
GRANT pipeshub_vetted_retrieval TO pipeshub_owner WITH SET TRUE;
GRANT USAGE ON SCHEMA retrieval TO pipeshub_vetted_retrieval;           -- the body names objects of its own schema
GRANT CREATE ON SCHEMA retrieval TO pipeshub_vetted_retrieval;
SET LOCAL ROLE pipeshub_vetted_retrieval;
CREATE FUNCTION retrieval.org_space_name(p_stamp text)
RETURNS TABLE (named_by text) LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, retrieval, pg_temp
SET standard_conforming_strings = on
AS $fn$
DECLARE v_org text := NULLIF(current_setting('app.org_id', true), '');
BEGIN
  IF p_stamp IS NULL THEN RAISE EXCEPTION 'org_space_name: p_stamp is required' USING ERRCODE = '22023'; END IF;
  IF length(p_stamp) NOT BETWEEN 1 AND 400 THEN RAISE EXCEPTION 'org_space_name: p_stamp length must be 1..400' USING ERRCODE = '22023'; END IF;
  IF v_org IS NULL THEN RETURN; END IF;       -- no org (or someone else's): no rows, the same answer
  UPDATE retrieval.org_spaces o SET named_by = p_stamp WHERE o.org_id = v_org AND o.role = 'active' AND o.named_by IS NULL;
  RETURN QUERY SELECT o.named_by FROM retrieval.org_spaces o WHERE o.org_id = v_org AND o.role = 'active' LIMIT 1;
END $fn$;
REVOKE ALL ON FUNCTION retrieval.org_space_name(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION retrieval.org_space_name(text) TO pipeshub_indexing;
SET LOCAL ROLE pipeshub_owner;
REVOKE CREATE ON SCHEMA retrieval FROM pipeshub_vetted_retrieval;
REVOKE pipeshub_vetted_retrieval FROM pipeshub_owner;
SELECT kernel.register_function('retrieval.org_space_name(text)', 'write', 'pipeshub_indexing', 'org', false);
