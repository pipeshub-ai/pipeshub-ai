-- generated from the manifest by python -m db.lib.vetted render: edit the manifest, not this file
GRANT pipeshub_vetted_retrieval TO pipeshub_owner WITH SET TRUE;
GRANT USAGE ON SCHEMA retrieval TO pipeshub_vetted_retrieval;           -- the body names objects of its own schema
GRANT CREATE ON SCHEMA retrieval TO pipeshub_vetted_retrieval;
SET LOCAL ROLE pipeshub_vetted_retrieval;
CREATE FUNCTION retrieval.search_sparse(p_collection smallint, p_terms bigint[], p_weights real[], p_apps text[], p_groups text[], p_roots text[], p_direct text[], p_must_vrids text[], p_leg int)
RETURNS TABLE (point_sk bigint, score double precision) LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, retrieval, pg_temp
SET standard_conforming_strings = on
SET plan_cache_mode = force_custom_plan
AS $fn$
DECLARE v_org text := NULLIF(current_setting('app.org_id', true), '');
BEGIN
  IF p_collection IS NULL THEN RAISE EXCEPTION 'search_sparse: p_collection is required' USING ERRCODE = '22023'; END IF;
  IF p_collection NOT BETWEEN 1 AND 32767 THEN RAISE EXCEPTION 'search_sparse: p_collection must be 1..32767' USING ERRCODE = '22023'; END IF;
  IF p_terms IS NULL THEN RAISE EXCEPTION 'search_sparse: p_terms is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_terms) > 20000 THEN RAISE EXCEPTION 'search_sparse: p_terms takes at most 20000 items' USING ERRCODE = '22023'; END IF;
  IF p_weights IS NULL THEN RAISE EXCEPTION 'search_sparse: p_weights is required' USING ERRCODE = '22023'; END IF;
  IF cardinality(p_weights) > 20000 THEN RAISE EXCEPTION 'search_sparse: p_weights takes at most 20000 items' USING ERRCODE = '22023'; END IF;
  IF p_apps IS NOT NULL AND cardinality(p_apps) > 10000 THEN RAISE EXCEPTION 'search_sparse: p_apps takes at most 10000 items' USING ERRCODE = '22023'; END IF;
  IF p_groups IS NOT NULL AND cardinality(p_groups) > 10000 THEN RAISE EXCEPTION 'search_sparse: p_groups takes at most 10000 items' USING ERRCODE = '22023'; END IF;
  IF p_roots IS NOT NULL AND cardinality(p_roots) > 10000 THEN RAISE EXCEPTION 'search_sparse: p_roots takes at most 10000 items' USING ERRCODE = '22023'; END IF;
  IF p_direct IS NOT NULL AND cardinality(p_direct) > 10000 THEN RAISE EXCEPTION 'search_sparse: p_direct takes at most 10000 items' USING ERRCODE = '22023'; END IF;
  IF p_must_vrids IS NOT NULL AND cardinality(p_must_vrids) > 100000 THEN RAISE EXCEPTION 'search_sparse: p_must_vrids takes at most 100000 items' USING ERRCODE = '22023'; END IF;
  IF p_leg IS NULL THEN RAISE EXCEPTION 'search_sparse: p_leg is required' USING ERRCODE = '22023'; END IF;
  IF p_leg NOT BETWEEN 1 AND 1000 THEN RAISE EXCEPTION 'search_sparse: p_leg must be 1..1000' USING ERRCODE = '22023'; END IF;
  IF v_org IS NULL THEN RETURN; END IF;       -- no org (or someone else's): no rows, the same answer
  DECLARE v_space smallint; v_n bigint; v_k int; v_sks bigint[]; v_scs double precision[]; v_total bigint;
  BEGIN
    IF cardinality(p_terms) < 1 OR cardinality(p_weights) <> cardinality(p_terms) THEN
      RAISE EXCEPTION 'search_sparse: invalid arguments' USING ERRCODE = '22023';
    END IF;
    SELECT o.space_id INTO v_space FROM retrieval.org_spaces o WHERE o.org_id = v_org AND o.role = 'active';
    IF v_space IS NULL THEN RETURN; END IF;
    SELECT coalesce((SELECT s.n_points FROM retrieval.collection_stats s
                      WHERE s.org_id = v_org AND s.collection_id = p_collection AND s.space_id = v_space), 0)
         + coalesce((SELECT sum(d.d) FROM retrieval.lexical_df_delta d
                      WHERE d.org_id = v_org AND d.collection_id = p_collection AND d.space_id = v_space AND d.term = -1), 0)
      INTO v_n;
      -- check the best points first (a top-N sort of the sums, not a full sort), best first, until p_leg pass: the point must exist
      -- and carry no tombstone whatever the filter, and pass the membership arms when there are any (all four NULL: the whole org);
      -- if the best v_k are not enough and there are more, go again over all of them
      FOREACH v_k IN ARRAY ARRAY[p_leg * 8, NULL] LOOP          -- the last pass has no bound (LIMIT NULL)
        WITH q AS MATERIALIZED (
          SELECT CASE WHEN t.term > 2147483647 THEN t.term - 4294967296 ELSE t.term END AS term, t.qw
            FROM unnest(p_terms, p_weights) AS t(term, qw) WHERE t.term IS NOT NULL AND t.qw IS NOT NULL),
        idf AS MATERIALIZED (
          SELECT q.term, q.qw * (ln(greatest(v_n, d.c) + 1) - ln(d.c + 0.5)) AS f
            FROM q CROSS JOIN LATERAL (SELECT count(*) AS c FROM retrieval.postings p
                                        WHERE p.org_id = v_org AND p.collection_id = p_collection AND p.space_id = v_space AND p.term = q.term) d
           WHERE d.c > 0),
        agg AS MATERIALIZED (
          SELECT x.point_sk, sum(i.f * x.w) AS sc
            FROM idf i CROSS JOIN LATERAL (
              SELECT p.point_sk, p.w FROM retrieval.postings p
               WHERE p.org_id = v_org AND p.collection_id = p_collection AND p.space_id = v_space AND p.term = i.term) x
           GROUP BY x.point_sk),
        kept AS (
          SELECT b.point_sk, b.sc FROM (SELECT a.point_sk, a.sc FROM agg a ORDER BY a.sc DESC, a.point_sk LIMIT v_k) b JOIN LATERAL (
            SELECT 1 AS ok FROM retrieval.points p
             WHERE p.org_id = v_org AND p.point_sk = b.point_sk
               AND (p_must_vrids IS NULL OR p.group_key = ANY(p_must_vrids))
               AND ((p_apps IS NULL AND p_groups IS NULL AND p_roots IS NULL AND p_direct IS NULL)
                    OR p.group_key = ANY(p_direct)
                    OR EXISTS (SELECT 1 FROM retrieval.group_members m
                                WHERE m.org_id = v_org AND m.collection_id = p_collection AND m.group_key = p.group_key
                                  AND ((m.kind = 1 AND m.member_id = ANY(p_apps)) OR (m.kind = 2 AND m.member_id = ANY(p_groups))
                                       OR (m.kind = 3 AND m.member_id = ANY(p_roots)))))
               AND NOT EXISTS (SELECT 1 FROM retrieval.point_tombstones x WHERE x.org_id = v_org AND x.point_sk = p.point_sk)
             LIMIT 1) m ON true
          ORDER BY b.sc DESC, b.point_sk
          LIMIT p_leg)
        SELECT coalesce(array_agg(k.point_sk ORDER BY k.sc DESC, k.point_sk), '{}'), coalesce(array_agg(k.sc ORDER BY k.sc DESC, k.point_sk), '{}'),
               (SELECT count(*) FROM (SELECT 1 FROM agg LIMIT coalesce(v_k + 1, 2147483647)) c)   -- only 'more than v_k?' is asked
          INTO v_sks, v_scs, v_total FROM kept k;
        IF cardinality(v_sks) >= p_leg OR v_k IS NULL OR v_total <= v_k THEN
          RETURN QUERY SELECT u.sk, u.sc FROM unnest(v_sks, v_scs) AS u(sk, sc) ORDER BY u.sc DESC, u.sk;
          RETURN;
        END IF;
      END LOOP;
  END;
END $fn$;
REVOKE ALL ON FUNCTION retrieval.search_sparse(smallint, bigint[], real[], text[], text[], text[], text[], text[], int) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION retrieval.search_sparse(smallint, bigint[], real[], text[], text[], text[], text[], text[], int) TO pipeshub_app;
SET LOCAL ROLE pipeshub_owner;
REVOKE CREATE ON SCHEMA retrieval FROM pipeshub_vetted_retrieval;
REVOKE pipeshub_vetted_retrieval FROM pipeshub_owner;
SELECT kernel.register_function('retrieval.search_sparse(smallint, bigint[], real[], text[], text[], text[], text[], text[], int)', 'read', NULL, 'org', false);
