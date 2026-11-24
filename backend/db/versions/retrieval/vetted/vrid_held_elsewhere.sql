-- generated from the manifest by python -m db.lib.vetted render: edit the manifest, not this file
GRANT pipeshub_vetted_retrieval TO pipeshub_owner WITH SET TRUE;
GRANT USAGE ON SCHEMA retrieval TO pipeshub_vetted_retrieval;           -- the body names objects of its own schema
GRANT CREATE ON SCHEMA retrieval TO pipeshub_vetted_retrieval;
SET LOCAL ROLE pipeshub_vetted_retrieval;
CREATE FUNCTION retrieval.vrid_held_elsewhere(p_vrid text)
RETURNS boolean LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, retrieval, pg_temp
SET standard_conforming_strings = on
SET plan_cache_mode = force_custom_plan
AS $fn$
DECLARE v_org text := NULLIF(current_setting('app.org_id', true), '');
BEGIN
  IF p_vrid IS NULL THEN RAISE EXCEPTION 'vrid_held_elsewhere: p_vrid is required' USING ERRCODE = '22023'; END IF;
  IF length(p_vrid) NOT BETWEEN 1 AND 2000 THEN RAISE EXCEPTION 'vrid_held_elsewhere: p_vrid length must be 1..2000' USING ERRCODE = '22023'; END IF;
  IF v_org IS NULL THEN RETURN true; END IF;       -- no org (or someone else's): no rows, the same answer
  -- the LATERAL ... LIMIT 1 keeps the points side a probe per (org, collection): a semi-join may scan every point for the vrid instead
  RETURN EXISTS (SELECT 1 FROM retrieval.org_spaces s CROSS JOIN retrieval.collections c
    CROSS JOIN LATERAL (SELECT 1 FROM retrieval.points p WHERE p.org_id = s.org_id AND p.collection_id = c.collection_id AND p.vrid = p_vrid
                          AND NOT EXISTS (SELECT 1 FROM retrieval.point_tombstones x WHERE x.org_id = p.org_id AND x.point_sk = p.point_sk)
                          LIMIT 1) hit
    WHERE s.org_id <> v_org AND c.status <> 'deleting');
END $fn$;
REVOKE ALL ON FUNCTION retrieval.vrid_held_elsewhere(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION retrieval.vrid_held_elsewhere(text) TO pipeshub_indexing;
SET LOCAL ROLE pipeshub_owner;
REVOKE CREATE ON SCHEMA retrieval FROM pipeshub_vetted_retrieval;
REVOKE pipeshub_vetted_retrieval FROM pipeshub_owner;
SELECT kernel.register_function('retrieval.vrid_held_elsewhere(text)', 'write', 'pipeshub_indexing', 'org', false);
