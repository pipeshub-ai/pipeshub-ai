-- The counters of the typed graph (kg.counts): per connector, how many rows each class holds of what a listing counts by class (facet 1, by class, kind and folder), and how many
-- live records carry each record type and origin (facet 2, the filters of a collection). Kept by delta rows: a statement trigger on kg.records adds, in the writer's own
-- transaction, one row per key the statement changed with the difference it made, so writers never contend on a hot counter row; the projection worker folds them into one
-- row per key (kg/counts.py). Summed per key, the rows are count(*) at every snapshot, folded or not. A connector's counts are exact once the worker has counted its rows whole
-- (its facet 0 row, n = 0): the rows written before that held no delta.
-- One table, not a delta table beside a folded one: the trigger runs as the vetted role (inside the vetted writers of kg.records), and that role may hold INSERT only on a table a
-- pinned writer inserts into (kernel check 124). The fold's writer inserts the folded rows here, so the deltas share its table and its pin.
CREATE SEQUENCE kg.count_id_seq AS bigint;

CREATE TABLE kg.counts (
  org_id text NOT NULL, id bigint NOT NULL DEFAULT nextval('kg.count_id_seq'), app_sk bigint NOT NULL, facet smallint NOT NULL CHECK (facet IN (0, 1, 2)),
  class_id bigint, kind smallint, folder boolean, record_type text, origin text, n bigint NOT NULL, folded boolean NOT NULL DEFAULT false, PRIMARY KEY (org_id, id)
);
CREATE INDEX counts_key ON kg.counts (org_id, app_sk, facet, class_id);         -- a connector's counts by class (a listing's total), its facets, and the keys a fold rewrites
CREATE INDEX counts_unfolded ON kg.counts (org_id, id) WHERE NOT folded;          -- the deltas a fold takes, oldest first
ALTER TABLE kg.counts SET (autovacuum_analyze_scale_factor = 0.02, autovacuum_analyze_threshold = 200, autovacuum_vacuum_scale_factor = 0.05, autovacuum_vacuum_insert_scale_factor = 0.02);

SELECT kernel.apply_rls('kg.counts', 'org', p_vetted_write => true);
SELECT kernel.require_vetted_org_policy('kg.counts');
GRANT SELECT ON kg.counts TO pipeshub_connectors, pipeshub_indexing, pipeshub_query;
GRANT USAGE ON SEQUENCE kg.count_id_seq TO pipeshub_vetted_kg;

-- The keys a set of kg.records rows adds to the counters, each with p_sign times its rows. Facet 1 counts what kg.listing counts by class (live, stamped, not hidden by a pending
-- restamp: listing.LIVE and listing.DECIDED); facet 2 the live records that are not folders. A row of no connector counts nowhere.
CREATE FUNCTION kg.count_keys(p_rows kg.records[], p_sign bigint)
RETURNS TABLE (org_id text, app_sk bigint, facet smallint, class_id bigint, kind smallint, folder boolean, record_type text, origin text, n bigint)
LANGUAGE sql IMMUTABLE PARALLEL SAFE SET search_path = pg_catalog, pg_temp AS $fn$
  SELECT r.org_id, r.app_sk, 1::smallint, r.class_id, r.kind, r.folder, NULL::text, NULL::text, p_sign * count(*) FROM unnest(p_rows) r
  WHERE r.app_sk IS NOT NULL AND r.kind IN (2, 3) AND NOT r.deleted AND NOT r.placeholder AND NOT r.alien
    AND r.r13 IS FALSE AND r.path IS NOT NULL AND r.class_id IS NOT NULL AND r.fence_job IS NULL
  GROUP BY r.org_id, r.app_sk, r.class_id, r.kind, r.folder
  UNION ALL
  SELECT r.org_id, r.app_sk, 2::smallint, NULL::bigint, NULL::smallint, NULL::boolean, r.record_type, r.origin, p_sign * count(*) FROM unnest(p_rows) r
  WHERE r.app_sk IS NOT NULL AND r.kind = 3 AND NOT r.folder AND NOT r.deleted AND NOT r.placeholder AND NOT r.alien
  GROUP BY r.org_id, r.app_sk, r.record_type, r.origin
$fn$;

-- PostgreSQL refuses transition tables on a trigger of several events, so: one function, three triggers. A statement that changes nothing counted adds no row.
-- A restore (db.restore_window: plain INSERTs as the restore role, triggers on) adds no delta, and the counter rows it carries are not kept (kg_counts_restore below): the
-- connector then has no facet 0 mark, so the worker counts it whole from the restored rows. Counters are derived data, and exported counters need not match the exported rows.
CREATE FUNCTION kg.count_change() RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, kg, pg_temp AS $fn$
BEGIN
  IF current_user = 'pipeshub_restore' THEN
    RETURN NULL;
  END IF;
  IF TG_OP = 'INSERT' THEN
    INSERT INTO kg.counts (org_id, app_sk, facet, class_id, kind, folder, record_type, origin, n)
      SELECT * FROM kg.count_keys((SELECT array_agg(t::kg.records) FROM newtab t), 1);
  ELSIF TG_OP = 'DELETE' THEN
    INSERT INTO kg.counts (org_id, app_sk, facet, class_id, kind, folder, record_type, origin, n)
      SELECT * FROM kg.count_keys((SELECT array_agg(t::kg.records) FROM oldtab t), -1);
  ELSE
    INSERT INTO kg.counts (org_id, app_sk, facet, class_id, kind, folder, record_type, origin, n)
      SELECT k.org_id, k.app_sk, k.facet, k.class_id, k.kind, k.folder, k.record_type, k.origin, sum(k.n) FROM (
        SELECT * FROM kg.count_keys((SELECT array_agg(t::kg.records) FROM newtab t), 1)
        UNION ALL SELECT * FROM kg.count_keys((SELECT array_agg(t::kg.records) FROM oldtab t), -1)) k
      GROUP BY k.org_id, k.app_sk, k.facet, k.class_id, k.kind, k.folder, k.record_type, k.origin HAVING sum(k.n) <> 0;
  END IF;
  RETURN NULL;
END $fn$;

SELECT kernel.exempt_trigger('kg.count_change()', 'inserts the counter deltas of the changed kg.records rows into kg.counts as the writer of kg.records (the vetted role), in its transaction, and reads nothing but its transition tables', 'security-review');

CREATE TRIGGER kg_records_count_insert AFTER INSERT ON kg.records REFERENCING NEW TABLE AS newtab FOR EACH STATEMENT EXECUTE FUNCTION kg.count_change();
CREATE TRIGGER kg_records_count_update AFTER UPDATE ON kg.records REFERENCING OLD TABLE AS oldtab NEW TABLE AS newtab FOR EACH STATEMENT EXECUTE FUNCTION kg.count_change();
CREATE TRIGGER kg_records_count_delete AFTER DELETE ON kg.records REFERENCING OLD TABLE AS oldtab FOR EACH STATEMENT EXECUTE FUNCTION kg.count_change();

CREATE FUNCTION kg.count_restored() RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $fn$
BEGIN
  RETURN NULL;
END $fn$;
CREATE TRIGGER kg_counts_restore BEFORE INSERT ON kg.counts FOR EACH ROW WHEN (current_user = 'pipeshub_restore') EXECUTE FUNCTION kg.count_restored();
