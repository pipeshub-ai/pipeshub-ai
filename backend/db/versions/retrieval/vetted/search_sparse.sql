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
  DECLARE v_space smallint; v_n bigint; v_kept boolean; v_tomb boolean; v_big boolean; v_tot bigint; v_k int; v_t bigint[]; v_f double precision[];
          v_ub double precision[]; v_df bigint[]; v_nt int; v_j int; v_i int; v_pj double precision; v_csk bigint[];
          v_csc double precision[]; v_nc int; v_lo int; v_hi int; v_w int; v_sks bigint[]; v_scs double precision[]; v_theta double precision;
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
    -- a term's frequency is kept (retrieval_0005: folded + deltas, in this statement's snapshot) unless the collection's postings
    -- predate it and recount has not run: then it is the number of the term's postings, counted, and its bound is not known
    SELECT NOT EXISTS (SELECT 1 FROM retrieval.lexical_df_pending x
                        WHERE x.org_id = v_org AND x.collection_id = p_collection AND x.space_id = v_space) INTO v_kept;
    SELECT EXISTS (SELECT 1 FROM retrieval.point_tombstones x WHERE x.org_id = v_org) INTO v_tomb;
    -- the query terms, most common first and those whose bound is not known last: f = query weight x idf, c the frequency,
    -- ub = |f| x the largest |weight|; a bound is not known when the folded row or any delta of the term lacks it
    SELECT coalesce(array_agg(s.term ORDER BY s.ub IS NULL, s.c DESC, s.o), '{}'), coalesce(array_agg(s.f ORDER BY s.ub IS NULL, s.c DESC, s.o), '{}'),
           coalesce(array_agg(s.ub ORDER BY s.ub IS NULL, s.c DESC, s.o), '{}'), coalesce(array_agg(s.c ORDER BY s.ub IS NULL, s.c DESC, s.o), '{}')
      INTO v_t, v_f, v_ub, v_df
      FROM (SELECT q.term, q.o, d.c, q.qw * (ln(greatest(v_n, d.c) + 1) - ln(d.c + 0.5)) AS f,
                   abs(q.qw * (ln(greatest(v_n, d.c) + 1) - ln(d.c + 0.5))) * d.wmax AS ub
              FROM (SELECT CASE WHEN t.term > 2147483647 THEN t.term - 4294967296 ELSE t.term END AS term, t.qw, t.o
                      FROM unnest(p_terms, p_weights) WITH ORDINALITY AS t(term, qw, o) WHERE t.term IS NOT NULL AND t.qw IS NOT NULL) q
              CROSS JOIN LATERAL (
                SELECT CASE WHEN v_kept THEN coalesce(f.df, 0) + coalesce(x.d, 0)
                            ELSE (SELECT count(*) FROM retrieval.postings p
                                   WHERE p.org_id = v_org AND p.collection_id = p_collection AND p.space_id = v_space AND p.term = q.term) END AS c,
                       CASE WHEN v_kept AND NOT coalesce(x.unknown, false) AND (f.term IS NULL OR f.wmax IS NOT NULL)
                            THEN greatest(f.wmax, x.wmax) END AS wmax
                  FROM (SELECT sum(t.d) AS d, max(t.wmax) AS wmax, bool_or(t.wmax IS NULL) AS unknown FROM retrieval.lexical_term_delta t
                         WHERE t.org_id = v_org AND t.collection_id = p_collection AND t.space_id = v_space AND t.term = q.term) x
                  LEFT JOIN retrieval.lexical_df f
                    ON f.org_id = v_org AND f.collection_id = p_collection AND f.space_id = v_space AND f.term = q.term
                 LIMIT 1) d       -- LIMIT keeps it from being pulled up: d.c is used three times and is read once per term
             WHERE d.c > 0) s;
    v_nt := cardinality(v_t);
    IF v_nt = 0 THEN RETURN; END IF;
    -- at most 200,000 postings in all: the exhaustive sum below (v_j = 0) is the cheapest way, unless the arms admit so few points
    -- (at most 100,000 divided by the number of terms, and at most twice as many probes as postings, each of which the sum would
    -- otherwise check) that scoring those points directly is cheaper
    -- (they are counted group by group, a duplicate twice)
    v_k := div(100000, v_nt);
    v_tot := (SELECT sum(u) FROM unnest(v_df) u);
    v_big := v_tot > 200000;
    IF NOT (p_apps IS NULL AND p_groups IS NULL AND p_roots IS NULL AND p_direct IS NULL) THEN
      SELECT count(*) INTO v_nc FROM (SELECT 1 FROM (
             SELECT m.group_key FROM retrieval.group_members m
              WHERE m.org_id = v_org AND m.collection_id = p_collection AND m.kind = 1 AND m.member_id = ANY(p_apps)
             UNION ALL SELECT m.group_key FROM retrieval.group_members m
              WHERE m.org_id = v_org AND m.collection_id = p_collection AND m.kind = 2 AND m.member_id = ANY(p_groups)
             UNION ALL SELECT m.group_key FROM retrieval.group_members m
              WHERE m.org_id = v_org AND m.collection_id = p_collection AND m.kind = 3 AND m.member_id = ANY(p_roots)
             UNION ALL SELECT unnest(p_direct)) g
           CROSS JOIN LATERAL (SELECT 1 FROM retrieval.points p
                                WHERE p.org_id = v_org AND p.collection_id = p_collection AND p.group_key = g.group_key LIMIT v_k + 1) x
           LIMIT v_k + 1) c;
    END IF;
    IF v_nc <= v_k AND (v_big OR v_nc * v_nt <= 2 * v_tot) THEN
      RETURN QUERY
        SELECT a.sk, sum(e.f * x.w) AS sc
          FROM (SELECT p.point_sk AS sk FROM (
                  SELECT m.group_key FROM retrieval.group_members m
                   WHERE m.org_id = v_org AND m.collection_id = p_collection AND m.kind = 1 AND m.member_id = ANY(p_apps)
                  UNION SELECT m.group_key FROM retrieval.group_members m
                   WHERE m.org_id = v_org AND m.collection_id = p_collection AND m.kind = 2 AND m.member_id = ANY(p_groups)
                  UNION SELECT m.group_key FROM retrieval.group_members m
                   WHERE m.org_id = v_org AND m.collection_id = p_collection AND m.kind = 3 AND m.member_id = ANY(p_roots)
                  UNION SELECT unnest(p_direct)) g
                JOIN retrieval.points p ON p.org_id = v_org AND p.collection_id = p_collection AND p.group_key = g.group_key
               WHERE p.space_id = v_space AND (p_must_vrids IS NULL OR p.group_key = ANY(p_must_vrids))
                 AND NOT (v_tomb AND EXISTS (SELECT 1 FROM retrieval.point_tombstones x WHERE x.org_id = v_org AND x.point_sk = p.point_sk))) a
         CROSS JOIN unnest(v_t, v_f) AS e(term, f)
         CROSS JOIN LATERAL (SELECT q.w FROM retrieval.postings q
                              WHERE q.org_id = v_org AND q.collection_id = p_collection AND q.space_id = v_space
                                AND q.term = e.term AND q.point_sk = a.sk LIMIT 1) x
         GROUP BY a.sk ORDER BY sc DESC, a.sk LIMIT p_leg;
      RETURN;
    END IF;
    -- v_j: how many of the most common terms stay out of the candidates (v_pj: the sum of their bounds). First the terms with a bound
    -- whose postings are more than 5 percent of the points, at most 8 (each candidate is probed for each of them), keeping one term in
    v_j := CASE WHEN v_big THEN least(v_nt - 1, 8, (SELECT count(*) FROM unnest(v_ub, v_df) AS u(ub, c) WHERE u.ub IS NOT NULL AND u.c > v_n * 0.05)) ELSE 0 END;
    LOOP
      v_pj := coalesce((SELECT sum(u) FROM unnest(v_ub[1:v_j]) u), 0);
      -- the candidates by partial sum, best first: the best p_leg * 128 (a top-N sort, the sums unstored), then, if they do not
      -- decide, all of them
      FOREACH v_k IN ARRAY ARRAY[p_leg * 128, NULL] LOOP
        SELECT coalesce(array_agg(a.sk ORDER BY a.sc DESC, a.sk), '{}'), coalesce(array_agg(a.sc ORDER BY a.sc DESC, a.sk), '{}')
          INTO v_csk, v_csc
          FROM (SELECT x.point_sk AS sk, sum(e.f * x.w) AS sc
                  FROM unnest(v_t[v_j + 1:v_nt], v_f[v_j + 1:v_nt]) AS e(term, f) CROSS JOIN LATERAL (
                    SELECT p.point_sk, p.w FROM retrieval.postings p
                     WHERE p.org_id = v_org AND p.collection_id = p_collection AND p.space_id = v_space AND p.term = e.term) x
                 GROUP BY x.point_sk ORDER BY sc DESC, sk LIMIT v_k) a;
        v_nc := cardinality(v_csk); v_sks := '{}'; v_scs := '{}'; v_theta := NULL; v_lo := 1; v_w := p_leg * 2;
        WHILE v_lo <= v_nc LOOP
          v_hi := least(v_lo + v_w - 1, v_nc);
          -- the window's points that pass (the point exists, carries no tombstone whatever the filter, passes the arms), scored on
          -- the left-out terms too, merged into the answer
          SELECT coalesce(array_agg(z.sk ORDER BY z.sc DESC, z.sk), '{}'), coalesce(array_agg(z.sc ORDER BY z.sc DESC, z.sk), '{}')
            INTO v_sks, v_scs
            FROM (SELECT y.sk, y.sc FROM (
                    SELECT c.sk, c.sc + coalesce((SELECT sum(n.f * x.w) FROM unnest(v_t[1:v_j], v_f[1:v_j]) AS n(term, f)
                                                    CROSS JOIN LATERAL (SELECT q.w FROM retrieval.postings q
                                                      WHERE q.org_id = v_org AND q.collection_id = p_collection AND q.space_id = v_space
                                                        AND q.term = n.term AND q.point_sk = c.sk LIMIT 1) x), 0) AS sc
                      FROM unnest(v_csk[v_lo:v_hi], v_csc[v_lo:v_hi]) AS c(sk, sc) JOIN LATERAL (
                        SELECT 1 AS ok FROM retrieval.points p
                         WHERE p.org_id = v_org AND p.point_sk = c.sk
                           AND (p_must_vrids IS NULL OR p.group_key = ANY(p_must_vrids))
                           AND ((p_apps IS NULL AND p_groups IS NULL AND p_roots IS NULL AND p_direct IS NULL)
                                OR p.group_key = ANY(p_direct)
                                OR EXISTS (SELECT 1 FROM retrieval.group_members m
                                            WHERE m.org_id = v_org AND m.collection_id = p_collection AND m.group_key = p.group_key
                                              AND ((m.kind = 1 AND m.member_id = ANY(p_apps)) OR (m.kind = 2 AND m.member_id = ANY(p_groups))
                                                   OR (m.kind = 3 AND m.member_id = ANY(p_roots)))))
                           AND NOT (v_tomb AND EXISTS (SELECT 1 FROM retrieval.point_tombstones x WHERE x.org_id = v_org AND x.point_sk = p.point_sk))
                         LIMIT 1) m ON true
                    UNION ALL SELECT u.sk, u.sc FROM unnest(v_sks, v_scs) AS u(sk, sc)) y
                  ORDER BY y.sc DESC, y.sk LIMIT p_leg) z;
          IF cardinality(v_sks) >= p_leg THEN v_theta := v_scs[p_leg]; END IF;
          v_lo := v_hi + 1; v_w := v_w * 2;
          -- a candidate scores at most its partial sum plus v_pj (a margin of 1e-9 covers the rounding of the sums)
          EXIT WHEN v_theta IS NOT NULL AND v_lo <= v_nc
                AND v_csc[v_lo] + v_pj + 1e-9 * (abs(v_csc[v_lo]) + abs(v_pj) + abs(v_theta)) < v_theta;
        END LOOP;
        -- decided: the pass held every candidate, or those it did not check (or hold) cannot reach the answer
        EXIT WHEN v_k IS NULL OR v_nc < v_k OR (v_theta IS NOT NULL
               AND v_csc[least(v_lo, v_nc)] + v_pj + 1e-9 * (abs(v_csc[least(v_lo, v_nc)]) + abs(v_pj) + abs(v_theta)) < v_theta);
      END LOOP;
      -- exact when no term is left out, or when a point that has none of the candidates' terms (it scores at most v_pj) cannot enter
      IF v_j = 0 OR (v_theta IS NOT NULL AND v_pj + 1e-9 * (abs(v_pj) + abs(v_theta)) < v_theta) THEN
        RETURN QUERY SELECT u.sk, u.sc FROM unnest(v_sks, v_scs) AS u(sk, sc) ORDER BY u.sc DESC, u.sk;
        RETURN;
      END IF;
      -- the least common left-out terms join: with a full answer, until the bounds of the rest sum below its last score (the next pass
      -- then decides); without one, the next one
      v_i := v_j - 1;
      IF v_theta IS NOT NULL THEN
        WHILE v_i > 0 AND (SELECT sum(u) FROM unnest(v_ub[1:v_i]) u) * (1 + 1e-9) + 1e-9 * abs(v_theta) >= v_theta LOOP
          v_i := v_i - 1;
        END LOOP;
      END IF;
      v_j := v_i;
    END LOOP;
  END;
END $fn$;
REVOKE ALL ON FUNCTION retrieval.search_sparse(smallint, bigint[], real[], text[], text[], text[], text[], text[], int) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION retrieval.search_sparse(smallint, bigint[], real[], text[], text[], text[], text[], text[], int) TO pipeshub_app;
SET LOCAL ROLE pipeshub_owner;
REVOKE CREATE ON SCHEMA retrieval FROM pipeshub_vetted_retrieval;
REVOKE pipeshub_vetted_retrieval FROM pipeshub_owner;
SELECT kernel.register_function('retrieval.search_sparse(smallint, bigint[], real[], text[], text[], text[], text[], text[], int)', 'read', NULL, 'org', false);
