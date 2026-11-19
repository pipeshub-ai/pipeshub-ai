-- generated from the manifest by python -m db.lib.vetted render: edit the manifest, not this file
GRANT pipeshub_vetted_retrieval TO pipeshub_owner WITH SET TRUE;
GRANT USAGE ON SCHEMA retrieval TO pipeshub_vetted_retrieval;           -- the body names objects of its own schema
GRANT CREATE ON SCHEMA retrieval TO pipeshub_vetted_retrieval;
SET LOCAL ROLE pipeshub_vetted_retrieval;
CREATE FUNCTION retrieval.vrid_orgs(p_vrids text[])
RETURNS TABLE (vrid text, org_id text) LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, retrieval, pg_temp
SET standard_conforming_strings = on
SET plan_cache_mode = force_custom_plan
AS $fn$
BEGIN
  IF p_vrids IS NULL THEN RAISE EXCEPTION 'vrid_orgs: p_vrids is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_vrids) > 100 THEN RAISE EXCEPTION 'vrid_orgs: p_vrids takes at most 100 items' USING ERRCODE = '22023'; END IF;
  DECLARE v_n int;
  BEGIN
    RETURN QUERY SELECT DISTINCT v.vrid, s.org_id
      FROM retrieval.org_spaces s
      CROSS JOIN retrieval.collections c
      CROSS JOIN unnest(p_vrids) AS v(vrid)
      CROSS JOIN LATERAL (SELECT 1 FROM retrieval.points p WHERE p.org_id = s.org_id AND p.collection_id = c.collection_id AND p.group_key = v.vrid LIMIT 1) hit
      WHERE s.role = 'active' AND c.status <> 'deleting'
      LIMIT 1001;
    GET DIAGNOSTICS v_n = ROW_COUNT;
    IF v_n > 1000 THEN RAISE EXCEPTION 'vrid_orgs: too many orgs for one batch' USING ERRCODE = '54000'; END IF;
  END;
END $fn$;
REVOKE ALL ON FUNCTION retrieval.vrid_orgs(text[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION retrieval.vrid_orgs(text[]) TO pipeshub_indexing;
SET LOCAL ROLE pipeshub_owner;
REVOKE CREATE ON SCHEMA retrieval FROM pipeshub_vetted_retrieval;
REVOKE pipeshub_vetted_retrieval FROM pipeshub_owner;
SELECT kernel.register_function('retrieval.vrid_orgs(text[])', 'write', 'pipeshub_indexing', 'global', false);
