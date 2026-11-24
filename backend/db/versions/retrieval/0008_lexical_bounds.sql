-- The largest |weight| of each term's postings, kept beside its document frequency (wmax), so the lexical search can bound what a term
-- adds to a score and skip summing the postings of the common terms (search_sparse, max-score pruning). It is an upper bound: a delete
-- leaves it as it was, and recount makes it exact again. The postings triggers write it on their delta rows (an insert, and an update that
-- changes a weight, carry the largest |weight| they wrote; a delete carries 0), the fold keeps the larger, and recount computes it.
-- NULL means not known: every row written before this revision, the deltas of a writer still running the old trigger body, and a
-- frequency row written by a fold or recount that does not keep the bound (lexical_df_bound). The search
-- does not prune a term whose folded row or any delta lacks it (the answer is the exhaustive sum, as before) until the fold worker has
-- filled its bound (or recount has computed every bound at once).
-- Locks in the order a fold takes them (the count tables, then the term tables), so a fold running during the upgrade waits for it
-- instead of deadlocking with it.
--
-- The collections that may hold a frequency row without a bound: the fold worker fills those rows from the postings in small batches
-- (db.lib.fold.fill_bounds) and removes the collection once none is left; a frequency row written without one (below) lists it again.
-- Filled with every collection that can have postings now, in the transaction that adds the columns: a fresh install starts empty.
CREATE TABLE retrieval.lexical_bound_pending (
  org_id text NOT NULL, collection_id smallint NOT NULL, space_id smallint NOT NULL,
  PRIMARY KEY (org_id, collection_id, space_id));
SELECT kernel.apply_rls('retrieval.lexical_bound_pending', 'org', p_jobs => true);
GRANT SELECT, INSERT, DELETE ON retrieval.lexical_bound_pending TO pipeshub_jobs;
-- Every collection that has postings has a point count (folded, or a delta: the writer of a posting writes both in one transaction).
ALTER TABLE retrieval.lexical_df_delta NO FORCE ROW LEVEL SECURITY;
ALTER TABLE retrieval.collection_stats NO FORCE ROW LEVEL SECURITY;
ALTER TABLE retrieval.lexical_bound_pending NO FORCE ROW LEVEL SECURITY;
INSERT INTO retrieval.lexical_bound_pending (org_id, collection_id, space_id)
  SELECT DISTINCT d.org_id, d.collection_id, d.space_id FROM retrieval.lexical_df_delta d WHERE d.term = -1
  UNION SELECT s.org_id, s.collection_id, s.space_id FROM retrieval.collection_stats s;
ALTER TABLE retrieval.lexical_df_delta FORCE ROW LEVEL SECURITY;
ALTER TABLE retrieval.collection_stats FORCE ROW LEVEL SECURITY;
ALTER TABLE retrieval.lexical_bound_pending FORCE ROW LEVEL SECURITY;

ALTER TABLE retrieval.lexical_term_delta ADD COLUMN wmax real;
ALTER TABLE retrieval.lexical_df ADD COLUMN wmax real;

-- Only the writers that keep the bound (this release's fold, recount and fill, which say so in their transaction) leave it as they
-- wrote it. Any other write of a frequency row (a fold or recount of an earlier release, still running during the upgrade or after a
-- rollback, which would fold a delta's bound away) makes it not known, and a row left without one lists its collection for the fold
-- worker. A restore keeps the rows it loads.
CREATE FUNCTION retrieval.lexical_df_bound() RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, retrieval, pg_temp AS $fn$
BEGIN
  IF current_user <> 'pipeshub_restore' AND current_setting('retrieval.bounds_kept', true) IS DISTINCT FROM 'on' THEN
    NEW.wmax := NULL;
  END IF;
  IF NEW.wmax IS NULL THEN
    INSERT INTO retrieval.lexical_bound_pending (org_id, collection_id, space_id) VALUES (NEW.org_id, NEW.collection_id, NEW.space_id)
      ON CONFLICT DO NOTHING;
  END IF;
  RETURN NEW;
END $fn$;
SELECT kernel.exempt_trigger('retrieval.lexical_df_bound()', 'clears the weight bound of a frequency row that a writer which does not keep '
  'it writes, and lists the row''s collection in retrieval.lexical_bound_pending, as the writer of the row, in its transaction', 'security-review');
CREATE TRIGGER lexical_df_bound BEFORE INSERT OR UPDATE ON retrieval.lexical_df FOR EACH ROW EXECUTE FUNCTION retrieval.lexical_df_bound();
-- The same writers delete the deltas they fold. One that does not keep the bound can fold a delta's bound away without writing the
-- frequency row (a weight that grew nets to no change in the frequency): the rows of the terms it folded lose their bound too.
CREATE FUNCTION retrieval.lexical_term_delta_bound() RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, retrieval, pg_temp AS $fn$
BEGIN
  IF current_user = 'pipeshub_restore' OR current_setting('retrieval.bounds_kept', true) IS NOT DISTINCT FROM 'on' THEN RETURN NULL; END IF;
  UPDATE retrieval.lexical_df f SET wmax = NULL
    FROM (SELECT DISTINCT o.org_id, o.collection_id, o.space_id, o.term FROM oldtab o) o
   WHERE f.org_id = o.org_id AND f.collection_id = o.collection_id AND f.space_id = o.space_id AND f.term = o.term AND f.wmax IS NOT NULL;
  RETURN NULL;
END $fn$;
SELECT kernel.exempt_trigger('retrieval.lexical_term_delta_bound()', 'clears the weight bound of the frequency rows of the terms whose deltas a '
  'writer which does not keep the bound deleted, as that writer, in its transaction; reads nothing but its transition table and those rows', 'security-review');
CREATE TRIGGER lexical_term_delta_bound AFTER DELETE ON retrieval.lexical_term_delta REFERENCING OLD TABLE AS oldtab
  FOR EACH STATEMENT EXECUTE FUNCTION retrieval.lexical_term_delta_bound();

CREATE OR REPLACE FUNCTION retrieval.postings_df_capture() RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, retrieval, pg_temp SET enable_nestloop = off SET jit = off AS $fn$
BEGIN
  IF current_user = 'pipeshub_restore' THEN RETURN NULL; END IF;
  IF TG_OP = 'INSERT' THEN
    INSERT INTO retrieval.lexical_term_delta (org_id, collection_id, space_id, term, d, wmax)
      SELECT n.org_id, n.collection_id, n.space_id, n.term, count(*), max(abs(n.w)) FROM newtab n GROUP BY 1, 2, 3, 4;
  ELSIF TG_OP = 'DELETE' THEN
    INSERT INTO retrieval.lexical_term_delta (org_id, collection_id, space_id, term, d, wmax)
      SELECT o.org_id, o.collection_id, o.space_id, o.term, -count(*), 0 FROM oldtab o GROUP BY 1, 2, 3, 4;
  ELSE        -- a weight change leaves the key alone and nets to nothing, but a larger weight raises the bound
    INSERT INTO retrieval.lexical_term_delta (org_id, collection_id, space_id, term, d, wmax)
      SELECT x.org_id, x.collection_id, x.space_id, x.term, sum(x.d), max(x.w) FROM (
        SELECT n.org_id, n.collection_id, n.space_id, n.term, 1 AS d, abs(n.w) AS w FROM newtab n
        UNION ALL SELECT o.org_id, o.collection_id, o.space_id, o.term, -1, 0 FROM oldtab o) x
      GROUP BY 1, 2, 3, 4 HAVING sum(x.d) <> 0 OR max(x.w) > 0;
  END IF;
  RETURN NULL;
END $fn$;
SELECT kernel.exempt_trigger('retrieval.postings_df_capture()', 'inserts the per-term change of the postings a statement wrote into retrieval.lexical_term_delta as the writer of the postings, in its transaction; reads nothing but its transition tables', 'security-review');
