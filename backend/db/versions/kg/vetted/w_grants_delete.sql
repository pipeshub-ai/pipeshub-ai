-- generated from the manifest by python -m db.lib.vetted render: edit the manifest, not this file
GRANT pipeshub_vetted_kg TO pipeshub_owner WITH SET TRUE;
GRANT USAGE ON SCHEMA kg TO pipeshub_vetted_kg;           -- the body names objects of its own schema
GRANT CREATE ON SCHEMA kg TO pipeshub_vetted_kg;
SET LOCAL ROLE pipeshub_vetted_kg;
CREATE FUNCTION kg.w_grants_delete(p_principals bigint[], p_nodes bigint[], p_gtypes text[])
RETURNS int LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, kg, pg_temp
SET standard_conforming_strings = on
SET plan_cache_mode = force_custom_plan
AS $fn$
DECLARE v_org text := NULLIF(current_setting('app.org_id', true), '');
BEGIN
  IF p_principals IS NULL THEN RAISE EXCEPTION 'w_grants_delete: p_principals is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_principals) > 5000 THEN RAISE EXCEPTION 'w_grants_delete: p_principals takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_principals) e WHERE e IS NULL) THEN RAISE EXCEPTION 'w_grants_delete: p_principals has a NULL item' USING ERRCODE = '22023'; END IF;
  IF p_nodes IS NULL THEN RAISE EXCEPTION 'w_grants_delete: p_nodes is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_nodes) > 5000 THEN RAISE EXCEPTION 'w_grants_delete: p_nodes takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_nodes) e WHERE e IS NULL) THEN RAISE EXCEPTION 'w_grants_delete: p_nodes has a NULL item' USING ERRCODE = '22023'; END IF;
  IF p_gtypes IS NULL THEN RAISE EXCEPTION 'w_grants_delete: p_gtypes is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_gtypes) > 5000 THEN RAISE EXCEPTION 'w_grants_delete: p_gtypes takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_gtypes) e WHERE e IS NULL) THEN RAISE EXCEPTION 'w_grants_delete: p_gtypes has a NULL item' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_gtypes) e WHERE e IS NOT NULL AND length(e) NOT BETWEEN 1 AND 64) THEN RAISE EXCEPTION 'w_grants_delete: p_gtypes has an item of the wrong length' USING ERRCODE = '22023'; END IF;
  IF v_org IS NULL THEN RETURN 0; END IF;       -- no org (or someone else's): no rows, the same answer
  DECLARE v_n integer;
  BEGIN
    IF cardinality(p_nodes) <> cardinality(p_principals) OR cardinality(p_gtypes) <> cardinality(p_principals) THEN
      RAISE EXCEPTION 'the arrays differ in length' USING ERRCODE = '22023';
    END IF;
    DELETE FROM kg.scope_members t USING unnest(p_principals, p_nodes, p_gtypes) AS k(a, b, c) WHERE t.org_id = v_org AND t.principal_sk = k.a AND t.node_sk = k.b AND t.gtype = k.c;
    GET DIAGNOSTICS v_n = ROW_COUNT;
    RETURN v_n;
  END;
END $fn$;
REVOKE ALL ON FUNCTION kg.w_grants_delete(bigint[], bigint[], text[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION kg.w_grants_delete(bigint[], bigint[], text[]) TO pipeshub_connectors;
SET LOCAL ROLE pipeshub_owner;
REVOKE CREATE ON SCHEMA kg FROM pipeshub_vetted_kg;
REVOKE pipeshub_vetted_kg FROM pipeshub_owner;
SELECT kernel.register_function('kg.w_grants_delete(bigint[], bigint[], text[])', 'write', 'pipeshub_connectors', 'org', false);
SELECT kernel.allow_vetted_write_op('kg.w_grants_delete(bigint[], bigint[], text[])', 'kg.scope_members', 'delete', ARRAY['principal_sk', 'node_sk', 'gtype']::name[], NULL, NULL, 5000);
