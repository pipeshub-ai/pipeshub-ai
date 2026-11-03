-- generated from the manifest by python -m db.lib.vetted render: edit the manifest, not this file
GRANT pipeshub_vetted_kg TO pipeshub_owner WITH SET TRUE;
GRANT USAGE ON SCHEMA kg TO pipeshub_vetted_kg;           -- the body names objects of its own schema
GRANT CREATE ON SCHEMA kg TO pipeshub_vetted_kg;
SET LOCAL ROLE pipeshub_vetted_kg;
CREATE FUNCTION kg.w_closure_delete(p_users bigint[], p_principals bigint[], p_apps bigint[])
RETURNS int LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, kg, pg_temp
SET standard_conforming_strings = on
SET plan_cache_mode = force_custom_plan
AS $fn$
DECLARE v_org text := NULLIF(current_setting('app.org_id', true), '');
BEGIN
  IF p_users IS NULL THEN RAISE EXCEPTION 'w_closure_delete: p_users is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_users) > 5000 THEN RAISE EXCEPTION 'w_closure_delete: p_users takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_users) e WHERE e IS NULL) THEN RAISE EXCEPTION 'w_closure_delete: p_users has a NULL item' USING ERRCODE = '22023'; END IF;
  IF p_principals IS NULL THEN RAISE EXCEPTION 'w_closure_delete: p_principals is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_principals) > 5000 THEN RAISE EXCEPTION 'w_closure_delete: p_principals takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_principals) e WHERE e IS NULL) THEN RAISE EXCEPTION 'w_closure_delete: p_principals has a NULL item' USING ERRCODE = '22023'; END IF;
  IF p_apps IS NULL THEN RAISE EXCEPTION 'w_closure_delete: p_apps is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_apps) > 5000 THEN RAISE EXCEPTION 'w_closure_delete: p_apps takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_apps) e WHERE e IS NULL) THEN RAISE EXCEPTION 'w_closure_delete: p_apps has a NULL item' USING ERRCODE = '22023'; END IF;
  IF v_org IS NULL THEN RETURN 0; END IF;       -- no org (or someone else's): no rows, the same answer
  DECLARE v_n integer;
  BEGIN
    IF cardinality(p_principals) <> cardinality(p_users) OR cardinality(p_apps) <> cardinality(p_users) THEN
      RAISE EXCEPTION 'the arrays differ in length' USING ERRCODE = '22023';
    END IF;
    DELETE FROM kg.principal_closure t USING unnest(p_users, p_principals, p_apps) AS k(a, b, c) WHERE t.org_id = v_org AND t.user_sk = k.a AND t.principal_sk = k.b AND t.app_sk = k.c;
    GET DIAGNOSTICS v_n = ROW_COUNT;
    RETURN v_n;
  END;
END $fn$;
REVOKE ALL ON FUNCTION kg.w_closure_delete(bigint[], bigint[], bigint[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION kg.w_closure_delete(bigint[], bigint[], bigint[]) TO pipeshub_connectors;
SET LOCAL ROLE pipeshub_owner;
REVOKE CREATE ON SCHEMA kg FROM pipeshub_vetted_kg;
REVOKE pipeshub_vetted_kg FROM pipeshub_owner;
SELECT kernel.register_function('kg.w_closure_delete(bigint[], bigint[], bigint[])', 'write', 'pipeshub_connectors', 'org', false);
SELECT kernel.allow_vetted_write_op('kg.w_closure_delete(bigint[], bigint[], bigint[])', 'kg.principal_closure', 'delete', ARRAY['user_sk', 'principal_sk', 'app_sk']::name[], NULL, NULL, 5000);
