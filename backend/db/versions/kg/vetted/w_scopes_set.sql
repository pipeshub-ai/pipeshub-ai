-- generated from the manifest by python -m db.lib.vetted render: edit the manifest, not this file
GRANT pipeshub_vetted_kg TO pipeshub_owner WITH SET TRUE;
GRANT USAGE ON SCHEMA kg TO pipeshub_vetted_kg;           -- the body names objects of its own schema
GRANT CREATE ON SCHEMA kg TO pipeshub_vetted_kg;
SET LOCAL ROLE pipeshub_vetted_kg;
CREATE FUNCTION kg.w_scopes_set(p_sks bigint[], p_kinds smallint[], p_app_sks bigint[], p_restricted boolean[], p_rule_restricted boolean[], p_needs_grant boolean[], p_deleted boolean[])
RETURNS int LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, kg, pg_temp
SET standard_conforming_strings = on
SET plan_cache_mode = force_custom_plan
AS $fn$
DECLARE v_org text := NULLIF(current_setting('app.org_id', true), '');
BEGIN
  IF p_sks IS NULL THEN RAISE EXCEPTION 'w_scopes_set: p_sks is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_sks) > 5000 THEN RAISE EXCEPTION 'w_scopes_set: p_sks takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_sks) e WHERE e IS NULL) THEN RAISE EXCEPTION 'w_scopes_set: p_sks has a NULL item' USING ERRCODE = '22023'; END IF;
  IF p_kinds IS NULL THEN RAISE EXCEPTION 'w_scopes_set: p_kinds is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_kinds) > 5000 THEN RAISE EXCEPTION 'w_scopes_set: p_kinds takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_kinds) e WHERE e IS NULL) THEN RAISE EXCEPTION 'w_scopes_set: p_kinds has a NULL item' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_kinds) e WHERE e IS NOT NULL AND e NOT BETWEEN 2 AND 3) THEN RAISE EXCEPTION 'w_scopes_set: p_kinds has an item out of range' USING ERRCODE = '22023'; END IF;
  IF p_app_sks IS NULL THEN RAISE EXCEPTION 'w_scopes_set: p_app_sks is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_app_sks) > 5000 THEN RAISE EXCEPTION 'w_scopes_set: p_app_sks takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_app_sks) e WHERE e IS NULL) THEN RAISE EXCEPTION 'w_scopes_set: p_app_sks has a NULL item' USING ERRCODE = '22023'; END IF;
  IF p_restricted IS NULL THEN RAISE EXCEPTION 'w_scopes_set: p_restricted is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_restricted) > 5000 THEN RAISE EXCEPTION 'w_scopes_set: p_restricted takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_restricted) e WHERE e IS NULL) THEN RAISE EXCEPTION 'w_scopes_set: p_restricted has a NULL item' USING ERRCODE = '22023'; END IF;
  IF p_rule_restricted IS NULL THEN RAISE EXCEPTION 'w_scopes_set: p_rule_restricted is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_rule_restricted) > 5000 THEN RAISE EXCEPTION 'w_scopes_set: p_rule_restricted takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_rule_restricted) e WHERE e IS NULL) THEN RAISE EXCEPTION 'w_scopes_set: p_rule_restricted has a NULL item' USING ERRCODE = '22023'; END IF;
  IF p_needs_grant IS NULL THEN RAISE EXCEPTION 'w_scopes_set: p_needs_grant is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_needs_grant) > 5000 THEN RAISE EXCEPTION 'w_scopes_set: p_needs_grant takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_needs_grant) e WHERE e IS NULL) THEN RAISE EXCEPTION 'w_scopes_set: p_needs_grant has a NULL item' USING ERRCODE = '22023'; END IF;
  IF p_deleted IS NULL THEN RAISE EXCEPTION 'w_scopes_set: p_deleted is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_deleted) > 5000 THEN RAISE EXCEPTION 'w_scopes_set: p_deleted takes at most 5000 items' USING ERRCODE = '22023'; END IF;
  IF EXISTS (SELECT 1 FROM unnest(p_deleted) e WHERE e IS NULL) THEN RAISE EXCEPTION 'w_scopes_set: p_deleted has a NULL item' USING ERRCODE = '22023'; END IF;
  IF v_org IS NULL THEN RETURN 0; END IF;       -- no org (or someone else's): no rows, the same answer
  DECLARE v_n integer;
  BEGIN
    IF cardinality(p_kinds) <> cardinality(p_sks) OR cardinality(p_app_sks) <> cardinality(p_sks) OR cardinality(p_restricted) <> cardinality(p_sks) OR cardinality(p_rule_restricted) <> cardinality(p_sks) OR cardinality(p_needs_grant) <> cardinality(p_sks) OR cardinality(p_deleted) <> cardinality(p_sks) THEN
      RAISE EXCEPTION 'the arrays differ in length' USING ERRCODE = '22023';
    END IF;
    INSERT INTO kg.scopes (org_id, sk, kind, app_sk, restricted, rule_restricted, needs_grant, deleted) SELECT v_org, k.a, k.b, k.c, k.d, k.e, k.f, k.g FROM unnest(p_sks, p_kinds, p_app_sks, p_restricted, p_rule_restricted, p_needs_grant, p_deleted) AS k(a, b, c, d, e, f, g)
      ON CONFLICT (org_id, sk) DO UPDATE SET kind = excluded.kind, app_sk = excluded.app_sk, restricted = excluded.restricted, rule_restricted = excluded.rule_restricted, needs_grant = excluded.needs_grant, deleted = excluded.deleted;
    GET DIAGNOSTICS v_n = ROW_COUNT;
    RETURN v_n;
  END;
END $fn$;
REVOKE ALL ON FUNCTION kg.w_scopes_set(bigint[], smallint[], bigint[], boolean[], boolean[], boolean[], boolean[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION kg.w_scopes_set(bigint[], smallint[], bigint[], boolean[], boolean[], boolean[], boolean[]) TO pipeshub_connectors;
SET LOCAL ROLE pipeshub_owner;
REVOKE CREATE ON SCHEMA kg FROM pipeshub_vetted_kg;
REVOKE pipeshub_vetted_kg FROM pipeshub_owner;
SELECT kernel.register_function('kg.w_scopes_set(bigint[], smallint[], bigint[], boolean[], boolean[], boolean[], boolean[])', 'write', 'pipeshub_connectors', 'org', false);
SELECT kernel.allow_vetted_write_op('kg.w_scopes_set(bigint[], smallint[], bigint[], boolean[], boolean[], boolean[], boolean[])', 'kg.scopes', 'upsert', NULL, ARRAY['kind', 'app_sk', 'restricted', 'rule_restricted', 'needs_grant', 'deleted']::name[], NULL, 5000);
