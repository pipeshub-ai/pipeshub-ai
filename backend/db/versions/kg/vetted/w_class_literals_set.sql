-- generated from the manifest by python -m db.lib.vetted render: edit the manifest, not this file
GRANT pipeshub_vetted_kg TO pipeshub_owner WITH SET TRUE;
GRANT USAGE ON SCHEMA kg TO pipeshub_vetted_kg;           -- the body names objects of its own schema
GRANT CREATE ON SCHEMA kg TO pipeshub_vetted_kg;
SET LOCAL ROLE pipeshub_vetted_kg;
CREATE FUNCTION kg.w_class_literals_set(p_anchors bigint[], p_classes bigint[])
RETURNS int LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, kg, pg_temp
SET standard_conforming_strings = on
SET plan_cache_mode = force_custom_plan
AS $fn$
DECLARE v_org text := NULLIF(current_setting('app.org_id', true), '');
BEGIN
  IF p_anchors IS NULL THEN RAISE EXCEPTION 'w_class_literals_set: p_anchors is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_anchors) > 5000 THEN RAISE EXCEPTION 'w_class_literals_set: p_anchors takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_anchors) e WHERE e IS NULL) THEN RAISE EXCEPTION 'w_class_literals_set: p_anchors has a NULL item' USING ERRCODE = '22023'; END IF;
  IF p_classes IS NULL THEN RAISE EXCEPTION 'w_class_literals_set: p_classes is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_classes) > 5000 THEN RAISE EXCEPTION 'w_class_literals_set: p_classes takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_classes) e WHERE e IS NULL) THEN RAISE EXCEPTION 'w_class_literals_set: p_classes has a NULL item' USING ERRCODE = '22023'; END IF;
  IF v_org IS NULL THEN RETURN 0; END IF;       -- no org (or someone else's): no rows, the same answer
  DECLARE v_n integer;
  BEGIN
    IF cardinality(p_classes) <> cardinality(p_anchors) THEN
      RAISE EXCEPTION 'the arrays differ in length' USING ERRCODE = '22023';
    END IF;
    INSERT INTO kg.class_literals (org_id, anchor_sk, class_id) SELECT v_org, k.a, k.b FROM unnest(p_anchors, p_classes) AS k(a, b)
      ON CONFLICT (org_id, anchor_sk, class_id) DO NOTHING;
    GET DIAGNOSTICS v_n = ROW_COUNT;
    RETURN v_n;
  END;
END $fn$;
REVOKE ALL ON FUNCTION kg.w_class_literals_set(bigint[], bigint[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION kg.w_class_literals_set(bigint[], bigint[]) TO pipeshub_connectors;
SET LOCAL ROLE pipeshub_owner;
REVOKE CREATE ON SCHEMA kg FROM pipeshub_vetted_kg;
REVOKE pipeshub_vetted_kg FROM pipeshub_owner;
SELECT kernel.register_function('kg.w_class_literals_set(bigint[], bigint[])', 'write', 'pipeshub_connectors', 'org', false);
SELECT kernel.allow_vetted_write_op('kg.w_class_literals_set(bigint[], bigint[])', 'kg.class_literals', 'upsert', NULL, ARRAY[]::name[], NULL, 5000);
