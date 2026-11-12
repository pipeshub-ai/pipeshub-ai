-- generated from the manifest by python -m db.lib.vetted render: edit the manifest, not this file
GRANT pipeshub_vetted_kg TO pipeshub_owner WITH SET TRUE;
GRANT USAGE ON SCHEMA kg TO pipeshub_vetted_kg;           -- the body names objects of its own schema
GRANT CREATE ON SCHEMA kg TO pipeshub_vetted_kg;
SET LOCAL ROLE pipeshub_vetted_kg;
CREATE FUNCTION kg.w_closure_set(p_users bigint[], p_principals bigint[], p_apps bigint[], p_vias text[])
RETURNS int LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, kg, pg_temp
SET standard_conforming_strings = on
SET plan_cache_mode = force_custom_plan
AS $fn$
DECLARE v_org text := NULLIF(current_setting('app.org_id', true), '');
BEGIN
  IF p_users IS NULL THEN RAISE EXCEPTION 'w_closure_set: p_users is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_users) > 5000 THEN RAISE EXCEPTION 'w_closure_set: p_users takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_users) e WHERE e IS NULL) THEN RAISE EXCEPTION 'w_closure_set: p_users has a NULL item' USING ERRCODE = '22023'; END IF;
  IF p_principals IS NULL THEN RAISE EXCEPTION 'w_closure_set: p_principals is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_principals) > 5000 THEN RAISE EXCEPTION 'w_closure_set: p_principals takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_principals) e WHERE e IS NULL) THEN RAISE EXCEPTION 'w_closure_set: p_principals has a NULL item' USING ERRCODE = '22023'; END IF;
  IF p_apps IS NULL THEN RAISE EXCEPTION 'w_closure_set: p_apps is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_apps) > 5000 THEN RAISE EXCEPTION 'w_closure_set: p_apps takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_apps) e WHERE e IS NULL) THEN RAISE EXCEPTION 'w_closure_set: p_apps has a NULL item' USING ERRCODE = '22023'; END IF;
  IF p_vias IS NULL THEN RAISE EXCEPTION 'w_closure_set: p_vias is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_vias) > 5000 THEN RAISE EXCEPTION 'w_closure_set: p_vias takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_vias) e WHERE e IS NULL) THEN RAISE EXCEPTION 'w_closure_set: p_vias has a NULL item' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_vias) e WHERE e IS NOT NULL AND e NOT IN ('self', 'membership', 'org', 'link', 'link_badge')) THEN RAISE EXCEPTION 'w_closure_set: p_vias has an item that is not one of the allowed values' USING ERRCODE = '22023'; END IF;
  IF v_org IS NULL THEN RETURN 0; END IF;       -- no org (or someone else's): no rows, the same answer
  DECLARE v_n integer;
  BEGIN
    IF cardinality(p_principals) <> cardinality(p_users) OR cardinality(p_apps) <> cardinality(p_users) OR cardinality(p_vias) <> cardinality(p_users) THEN
      RAISE EXCEPTION 'the arrays differ in length' USING ERRCODE = '22023';
    END IF;
    -- Sorted by key (locks are taken in key order) and a key named twice keeps its last item
    SELECT array_agg(s.c1 ORDER BY s.c1, s.c2, s.c3), array_agg(s.c2 ORDER BY s.c1, s.c2, s.c3), array_agg(s.c3 ORDER BY s.c1, s.c2, s.c3), array_agg(s.c4 ORDER BY s.c1, s.c2, s.c3) INTO p_users, p_principals, p_apps, p_vias FROM (SELECT DISTINCT ON (k.c1, k.c2, k.c3) k.c1, k.c2, k.c3, k.c4 FROM unnest(p_users, p_principals, p_apps, p_vias) WITH ORDINALITY AS k(c1, c2, c3, c4, n) ORDER BY k.c1, k.c2, k.c3, k.n DESC) s;
    INSERT INTO kg.principal_closure (org_id, user_sk, principal_sk, app_sk, via) SELECT v_org, k.a, k.b, k.c, k.d FROM unnest(p_users, p_principals, p_apps, p_vias) AS k(a, b, c, d)
      ON CONFLICT (org_id, user_sk, principal_sk, app_sk) DO UPDATE SET via = excluded.via;
    GET DIAGNOSTICS v_n = ROW_COUNT;
    RETURN v_n;
  END;
END $fn$;
REVOKE ALL ON FUNCTION kg.w_closure_set(bigint[], bigint[], bigint[], text[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION kg.w_closure_set(bigint[], bigint[], bigint[], text[]) TO pipeshub_connectors;
SET LOCAL ROLE pipeshub_owner;
REVOKE CREATE ON SCHEMA kg FROM pipeshub_vetted_kg;
REVOKE pipeshub_vetted_kg FROM pipeshub_owner;
SELECT kernel.register_function('kg.w_closure_set(bigint[], bigint[], bigint[], text[])', 'write', 'pipeshub_connectors', 'org', false);
SELECT kernel.allow_vetted_write_op('kg.w_closure_set(bigint[], bigint[], bigint[], text[])', 'kg.principal_closure', 'upsert', NULL, ARRAY['via']::name[], NULL, 5000);
