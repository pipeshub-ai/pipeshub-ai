-- Facet 2 of the counters (live records by record type and origin, kg_0015) has no reader: a collection's filters also need the connector names and indexing states, which kg
-- does not hold, so that listing reads them from the graph with its page. kg.count_keys stops adding its deltas and its rows go. The facet value 2 stays allowed (the table's
-- check, kg.w_counts_add), so the seed of an earlier release, which still writes facet 2 rows, runs unchanged; nothing reads them.
CREATE OR REPLACE FUNCTION kg.count_keys(p_rows kg.records[], p_sign bigint)
RETURNS TABLE (org_id text, app_sk bigint, facet smallint, class_id bigint, kind smallint, folder boolean, record_type text, origin text, n bigint)
LANGUAGE sql IMMUTABLE PARALLEL SAFE SET search_path = pg_catalog, pg_temp AS $fn$
  SELECT r.org_id, r.app_sk, 1::smallint, r.class_id, r.kind, r.folder, NULL::text, NULL::text, p_sign * count(*) FROM unnest(p_rows) r
  WHERE r.app_sk IS NOT NULL AND r.kind IN (2, 3) AND NOT r.deleted AND NOT r.placeholder AND NOT r.alien
    AND r.r13 IS FALSE AND r.path IS NOT NULL AND r.class_id IS NOT NULL AND r.fence_job IS NULL
  GROUP BY r.org_id, r.app_sk, r.class_id, r.kind, r.folder
$fn$;

-- Every org's rows: the table's row security is lifted for this statement alone, as kg_0019 does for kg.projection_state.
ALTER TABLE kg.counts NO FORCE ROW LEVEL SECURITY;
DELETE FROM kg.counts WHERE facet = 2;
ALTER TABLE kg.counts FORCE ROW LEVEL SECURITY;
