-- generated from the manifest by python -m db.lib.vetted render: edit the manifest, not this file
GRANT pipeshub_vetted_kg TO pipeshub_owner WITH SET TRUE;
GRANT USAGE ON SCHEMA kg TO pipeshub_vetted_kg;           -- the body names objects of its own schema
GRANT CREATE ON SCHEMA kg TO pipeshub_vetted_kg;
SET LOCAL ROLE pipeshub_vetted_kg;
CREATE FUNCTION kg.w_grant_marks_set(p_principals bigint[], p_nodes bigint[], p_gtypes text[], p_helds boolean[], p_groups bigint[])
RETURNS int LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, kg, pg_temp
SET standard_conforming_strings = on
SET plan_cache_mode = force_custom_plan
AS $fn$
DECLARE v_org text := NULLIF(current_setting('app.org_id', true), '');
BEGIN
  IF p_principals IS NULL THEN RAISE EXCEPTION 'w_grant_marks_set: p_principals is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_principals) > 5000 THEN RAISE EXCEPTION 'w_grant_marks_set: p_principals takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_principals) e WHERE e IS NULL) THEN RAISE EXCEPTION 'w_grant_marks_set: p_principals has a NULL item' USING ERRCODE = '22023'; END IF;
  IF p_nodes IS NULL THEN RAISE EXCEPTION 'w_grant_marks_set: p_nodes is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_nodes) > 5000 THEN RAISE EXCEPTION 'w_grant_marks_set: p_nodes takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_nodes) e WHERE e IS NULL) THEN RAISE EXCEPTION 'w_grant_marks_set: p_nodes has a NULL item' USING ERRCODE = '22023'; END IF;
  IF p_gtypes IS NULL THEN RAISE EXCEPTION 'w_grant_marks_set: p_gtypes is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_gtypes) > 5000 THEN RAISE EXCEPTION 'w_grant_marks_set: p_gtypes takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_gtypes) e WHERE e IS NULL) THEN RAISE EXCEPTION 'w_grant_marks_set: p_gtypes has a NULL item' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_gtypes) e WHERE e IS NOT NULL AND length(e) NOT BETWEEN 1 AND 64) THEN RAISE EXCEPTION 'w_grant_marks_set: p_gtypes has an item of the wrong length' USING ERRCODE = '22023'; END IF;
  IF p_helds IS NULL THEN RAISE EXCEPTION 'w_grant_marks_set: p_helds is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_helds) > 5000 THEN RAISE EXCEPTION 'w_grant_marks_set: p_helds takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_helds) e WHERE e IS NULL) THEN RAISE EXCEPTION 'w_grant_marks_set: p_helds has a NULL item' USING ERRCODE = '22023'; END IF;
  IF p_groups IS NULL THEN RAISE EXCEPTION 'w_grant_marks_set: p_groups is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_groups) > 5000 THEN RAISE EXCEPTION 'w_grant_marks_set: p_groups takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF v_org IS NULL THEN RETURN 0; END IF;       -- no org (or someone else's): no rows, the same answer
  DECLARE v_n integer := 0; v_c integer; v_p bigint; v_nd bigint; v_g text; v_h boolean; v_grp bigint;
  BEGIN
    IF cardinality(p_nodes) <> cardinality(p_principals) OR cardinality(p_gtypes) <> cardinality(p_principals) OR cardinality(p_helds) <> cardinality(p_principals) OR cardinality(p_groups) <> cardinality(p_principals) THEN
      RAISE EXCEPTION 'the arrays differ in length' USING ERRCODE = '22023';
    END IF;
    -- Sorted by key (locks are taken in key order) and a key named twice keeps its last item
    SELECT array_agg(s.c1 ORDER BY s.c1, s.c2, s.c3), array_agg(s.c2 ORDER BY s.c1, s.c2, s.c3), array_agg(s.c3 ORDER BY s.c1, s.c2, s.c3), array_agg(s.c4 ORDER BY s.c1, s.c2, s.c3), array_agg(s.c5 ORDER BY s.c1, s.c2, s.c3) INTO p_principals, p_nodes, p_gtypes, p_helds, p_groups FROM (SELECT DISTINCT ON (k.c1, k.c2, k.c3) k.c1, k.c2, k.c3, k.c4, k.c5 FROM unnest(p_principals, p_nodes, p_gtypes, p_helds, p_groups) WITH ORDINALITY AS k(c1, c2, c3, c4, c5, n) ORDER BY k.c1, k.c2, k.c3, k.n DESC) s;
    -- An update only: a grant another unit deleted since the caller read it is not made again
    FOR i IN 1 .. coalesce(cardinality(p_principals), 0) LOOP
      v_p := p_principals[i]; v_nd := p_nodes[i]; v_g := p_gtypes[i]; v_h := p_helds[i]; v_grp := p_groups[i];
      UPDATE kg.scope_members m SET parent_held = v_h, node_group = v_grp WHERE m.org_id = v_org AND m.principal_sk = v_p AND m.node_sk = v_nd AND m.gtype = v_g;
      GET DIAGNOSTICS v_c = ROW_COUNT;
      v_n := v_n + v_c;
    END LOOP;
    RETURN v_n;
  END;
END $fn$;
REVOKE ALL ON FUNCTION kg.w_grant_marks_set(bigint[], bigint[], text[], boolean[], bigint[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION kg.w_grant_marks_set(bigint[], bigint[], text[], boolean[], bigint[]) TO pipeshub_connectors;
SET LOCAL ROLE pipeshub_owner;
REVOKE CREATE ON SCHEMA kg FROM pipeshub_vetted_kg;
REVOKE pipeshub_vetted_kg FROM pipeshub_owner;
SELECT kernel.register_function('kg.w_grant_marks_set(bigint[], bigint[], text[], boolean[], bigint[])', 'write', 'pipeshub_connectors', 'org', false);
SELECT kernel.allow_vetted_write_op('kg.w_grant_marks_set(bigint[], bigint[], text[], boolean[], bigint[])', 'kg.scope_members', 'update', NULL, NULL, NULL, 5000);
