-- generated from the manifest by python -m db.lib.vetted render: edit the manifest, not this file
GRANT pipeshub_vetted_kg TO pipeshub_owner WITH SET TRUE;
GRANT USAGE ON SCHEMA kg TO pipeshub_vetted_kg;           -- the body names objects of its own schema
GRANT CREATE ON SCHEMA kg TO pipeshub_vetted_kg;
SET LOCAL ROLE pipeshub_vetted_kg;
CREATE FUNCTION kg.w_grants_set(p_principals bigint[], p_nodes bigint[], p_gtypes text[], p_roles text[], p_apps bigint[], p_leaves boolean[])
RETURNS int LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, kg, pg_temp
SET standard_conforming_strings = on
SET plan_cache_mode = force_custom_plan
AS $fn$
DECLARE v_org text := NULLIF(current_setting('app.org_id', true), '');
BEGIN
  IF p_principals IS NULL THEN RAISE EXCEPTION 'w_grants_set: p_principals is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_principals) > 5000 THEN RAISE EXCEPTION 'w_grants_set: p_principals takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_principals) e WHERE e IS NULL) THEN RAISE EXCEPTION 'w_grants_set: p_principals has a NULL item' USING ERRCODE = '22023'; END IF;
  IF p_nodes IS NULL THEN RAISE EXCEPTION 'w_grants_set: p_nodes is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_nodes) > 5000 THEN RAISE EXCEPTION 'w_grants_set: p_nodes takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_nodes) e WHERE e IS NULL) THEN RAISE EXCEPTION 'w_grants_set: p_nodes has a NULL item' USING ERRCODE = '22023'; END IF;
  IF p_gtypes IS NULL THEN RAISE EXCEPTION 'w_grants_set: p_gtypes is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_gtypes) > 5000 THEN RAISE EXCEPTION 'w_grants_set: p_gtypes takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_gtypes) e WHERE e IS NULL) THEN RAISE EXCEPTION 'w_grants_set: p_gtypes has a NULL item' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_gtypes) e WHERE e IS NOT NULL AND length(e) NOT BETWEEN 1 AND 64) THEN RAISE EXCEPTION 'w_grants_set: p_gtypes has an item of the wrong length' USING ERRCODE = '22023'; END IF;
  IF p_roles IS NULL THEN RAISE EXCEPTION 'w_grants_set: p_roles is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_roles) > 5000 THEN RAISE EXCEPTION 'w_grants_set: p_roles takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_roles) e WHERE e IS NOT NULL AND length(e) NOT BETWEEN 0 AND 64) THEN RAISE EXCEPTION 'w_grants_set: p_roles has an item of the wrong length' USING ERRCODE = '22023'; END IF;
  IF p_apps IS NULL THEN RAISE EXCEPTION 'w_grants_set: p_apps is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_apps) > 5000 THEN RAISE EXCEPTION 'w_grants_set: p_apps takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF p_leaves IS NULL THEN RAISE EXCEPTION 'w_grants_set: p_leaves is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_leaves) > 5000 THEN RAISE EXCEPTION 'w_grants_set: p_leaves takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_leaves) e WHERE e IS NULL) THEN RAISE EXCEPTION 'w_grants_set: p_leaves has a NULL item' USING ERRCODE = '22023'; END IF;
  IF v_org IS NULL THEN RETURN 0; END IF;       -- no org (or someone else's): no rows, the same answer
  DECLARE v_n integer;
  BEGIN
    IF cardinality(p_nodes) <> cardinality(p_principals) OR cardinality(p_gtypes) <> cardinality(p_principals) OR cardinality(p_roles) <> cardinality(p_principals) OR cardinality(p_apps) <> cardinality(p_principals) OR cardinality(p_leaves) <> cardinality(p_principals) THEN
      RAISE EXCEPTION 'the arrays differ in length' USING ERRCODE = '22023';
    END IF;
    -- Sorted by key (locks are taken in key order) and a key named twice keeps its last item
    SELECT array_agg(s.c1 ORDER BY s.c1, s.c2, s.c3), array_agg(s.c2 ORDER BY s.c1, s.c2, s.c3), array_agg(s.c3 ORDER BY s.c1, s.c2, s.c3), array_agg(s.c4 ORDER BY s.c1, s.c2, s.c3), array_agg(s.c5 ORDER BY s.c1, s.c2, s.c3), array_agg(s.c6 ORDER BY s.c1, s.c2, s.c3) INTO p_principals, p_nodes, p_gtypes, p_roles, p_apps, p_leaves FROM (SELECT DISTINCT ON (k.c1, k.c2, k.c3) k.c1, k.c2, k.c3, k.c4, k.c5, k.c6 FROM unnest(p_principals, p_nodes, p_gtypes, p_roles, p_apps, p_leaves) WITH ORDINALITY AS k(c1, c2, c3, c4, c5, c6, n) ORDER BY k.c1, k.c2, k.c3, k.n DESC) s;
    INSERT INTO kg.scope_members (org_id, principal_sk, node_sk, gtype, role, app_sk, leaf) SELECT v_org, k.a, k.b, k.c, k.d, k.e, k.f FROM unnest(p_principals, p_nodes, p_gtypes, p_roles, p_apps, p_leaves) AS k(a, b, c, d, e, f)
      ON CONFLICT (org_id, principal_sk, node_sk, gtype) DO UPDATE SET role = excluded.role, app_sk = excluded.app_sk, leaf = excluded.leaf;
    GET DIAGNOSTICS v_n = ROW_COUNT;
    RETURN v_n;
  END;
END $fn$;
REVOKE ALL ON FUNCTION kg.w_grants_set(bigint[], bigint[], text[], text[], bigint[], boolean[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION kg.w_grants_set(bigint[], bigint[], text[], text[], bigint[], boolean[]) TO pipeshub_connectors;
SET LOCAL ROLE pipeshub_owner;
REVOKE CREATE ON SCHEMA kg FROM pipeshub_vetted_kg;
REVOKE pipeshub_vetted_kg FROM pipeshub_owner;
SELECT kernel.register_function('kg.w_grants_set(bigint[], bigint[], text[], text[], bigint[], boolean[])', 'write', 'pipeshub_connectors', 'org', false);
SELECT kernel.allow_vetted_write_op('kg.w_grants_set(bigint[], bigint[], text[], text[], bigint[], boolean[])', 'kg.scope_members', 'upsert', NULL, ARRAY['role', 'app_sk', 'leaf']::name[], NULL, 5000);
