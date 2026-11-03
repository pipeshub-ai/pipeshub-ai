-- generated from the manifest by python -m db.lib.vetted render: edit the manifest, not this file
GRANT pipeshub_vetted_kg TO pipeshub_owner WITH SET TRUE;
GRANT USAGE ON SCHEMA kg TO pipeshub_vetted_kg;           -- the body names objects of its own schema
GRANT CREATE ON SCHEMA kg TO pipeshub_vetted_kg;
SET LOCAL ROLE pipeshub_vetted_kg;
CREATE FUNCTION kg.w_links_set(p_parents bigint[], p_kinds smallint[], p_children bigint[])
RETURNS int LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, kg, pg_temp
SET standard_conforming_strings = on
SET plan_cache_mode = force_custom_plan
AS $fn$
DECLARE v_org text := NULLIF(current_setting('app.org_id', true), '');
BEGIN
  IF p_parents IS NULL THEN RAISE EXCEPTION 'w_links_set: p_parents is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_parents) > 5000 THEN RAISE EXCEPTION 'w_links_set: p_parents takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_parents) e WHERE e IS NULL) THEN RAISE EXCEPTION 'w_links_set: p_parents has a NULL item' USING ERRCODE = '22023'; END IF;
  IF p_kinds IS NULL THEN RAISE EXCEPTION 'w_links_set: p_kinds is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_kinds) > 5000 THEN RAISE EXCEPTION 'w_links_set: p_kinds takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_kinds) e WHERE e IS NULL) THEN RAISE EXCEPTION 'w_links_set: p_kinds has a NULL item' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_kinds) e WHERE e IS NOT NULL AND e NOT BETWEEN 1 AND 2) THEN RAISE EXCEPTION 'w_links_set: p_kinds has an item out of range' USING ERRCODE = '22023'; END IF;
  IF p_children IS NULL THEN RAISE EXCEPTION 'w_links_set: p_children is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_children) > 5000 THEN RAISE EXCEPTION 'w_links_set: p_children takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_children) e WHERE e IS NULL) THEN RAISE EXCEPTION 'w_links_set: p_children has a NULL item' USING ERRCODE = '22023'; END IF;
  IF v_org IS NULL THEN RETURN 0; END IF;       -- no org (or someone else's): no rows, the same answer
  DECLARE v_n integer;
  BEGIN
    IF cardinality(p_kinds) <> cardinality(p_parents) OR cardinality(p_children) <> cardinality(p_parents) THEN
      RAISE EXCEPTION 'the arrays differ in length' USING ERRCODE = '22023';
    END IF;
    INSERT INTO kg.links (org_id, parent_sk, kind, child_sk) SELECT v_org, k.a, k.b, k.c FROM unnest(p_parents, p_kinds, p_children) AS k(a, b, c)
      ON CONFLICT (org_id, parent_sk, kind, child_sk) DO NOTHING;
    GET DIAGNOSTICS v_n = ROW_COUNT;
    RETURN v_n;
  END;
END $fn$;
REVOKE ALL ON FUNCTION kg.w_links_set(bigint[], smallint[], bigint[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION kg.w_links_set(bigint[], smallint[], bigint[]) TO pipeshub_connectors;
SET LOCAL ROLE pipeshub_owner;
REVOKE CREATE ON SCHEMA kg FROM pipeshub_vetted_kg;
REVOKE pipeshub_vetted_kg FROM pipeshub_owner;
SELECT kernel.register_function('kg.w_links_set(bigint[], smallint[], bigint[])', 'write', 'pipeshub_connectors', 'org', false);
SELECT kernel.allow_vetted_write_op('kg.w_links_set(bigint[], smallint[], bigint[])', 'kg.links', 'upsert', NULL, ARRAY[]::name[], NULL, 5000);
