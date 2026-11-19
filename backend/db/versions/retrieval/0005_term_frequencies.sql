-- The document frequency of each term, kept beside the postings so that a lexical search reads it instead of counting the postings of
-- each query term (that count read every posting a second time: about a fifth of the search's work on a 57,000-point corpus).
-- It is kept by the database, not by the writers: statement triggers on retrieval.postings add one row per term and statement to
-- lexical_term_delta, in the writer's transaction, whatever code wrote the postings. So the frequency a search reads (folded + deltas,
-- in its own snapshot) is the number of postings that snapshot sees, and no row lock is shared by two writers of a common term.
-- The fold (python -m db.maintain fold) moves the deltas into lexical_df; recount recomputes both from the postings.
-- A delta table of its own, not lexical_df_delta: older fold and recount code sums every row of that table into the point count.
CREATE TABLE retrieval.lexical_df (
  org_id text NOT NULL, collection_id smallint NOT NULL, space_id smallint NOT NULL, term integer NOT NULL, df bigint NOT NULL,
  PRIMARY KEY (org_id, collection_id, space_id, term));
CREATE TABLE retrieval.lexical_term_delta (
  org_id text NOT NULL, collection_id smallint NOT NULL, space_id smallint NOT NULL, term integer NOT NULL, d integer NOT NULL,
  seq bigint NOT NULL GENERATED ALWAYS AS IDENTITY,
  PRIMARY KEY (org_id, seq));
CREATE INDEX lexical_term_delta_term ON retrieval.lexical_term_delta (org_id, collection_id, space_id, term) INCLUDE (d);
-- The collections whose postings were written before the triggers existed: their search counts the postings until recount has
-- computed their frequencies. Filled below, in the transaction that creates the triggers, which waits for every open writer of postings.
CREATE TABLE retrieval.lexical_df_pending (
  org_id text NOT NULL, collection_id smallint NOT NULL, space_id smallint NOT NULL,
  PRIMARY KEY (org_id, collection_id, space_id));

SELECT kernel.apply_rls('retrieval.lexical_df', 'org', p_jobs => true, p_vetted => true);
SELECT kernel.apply_rls('retrieval.lexical_term_delta', 'org', p_jobs => true, p_vetted => true);
SELECT kernel.apply_rls('retrieval.lexical_df_pending', 'org', p_jobs => true, p_vetted => true);
GRANT INSERT ON retrieval.lexical_term_delta TO pipeshub_indexing;       -- the triggers run as the role that writes the postings
GRANT SELECT, INSERT, UPDATE, DELETE ON retrieval.lexical_df, retrieval.lexical_term_delta, retrieval.lexical_df_pending TO pipeshub_jobs;

-- A restore loads the exported frequencies and deltas as they were, so the postings it loads add none.
-- enable_nestloop is off: a transition table has no statistics, and the join of an update's two images would be planned as a nested loop.
CREATE FUNCTION retrieval.postings_df_capture() RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, retrieval, pg_temp SET enable_nestloop = off SET jit = off AS $fn$
BEGIN
  IF current_user = 'pipeshub_restore' THEN RETURN NULL; END IF;
  IF TG_OP = 'INSERT' THEN
    INSERT INTO retrieval.lexical_term_delta (org_id, collection_id, space_id, term, d)
      SELECT n.org_id, n.collection_id, n.space_id, n.term, count(*) FROM newtab n GROUP BY 1, 2, 3, 4;
  ELSIF TG_OP = 'DELETE' THEN
    INSERT INTO retrieval.lexical_term_delta (org_id, collection_id, space_id, term, d)
      SELECT o.org_id, o.collection_id, o.space_id, o.term, -count(*) FROM oldtab o GROUP BY 1, 2, 3, 4;
  ELSE        -- a weight change leaves the key alone and nets to nothing
    INSERT INTO retrieval.lexical_term_delta (org_id, collection_id, space_id, term, d)
      SELECT x.org_id, x.collection_id, x.space_id, x.term, sum(x.d) FROM (
        SELECT n.org_id, n.collection_id, n.space_id, n.term, 1 AS d FROM newtab n
        UNION ALL SELECT o.org_id, o.collection_id, o.space_id, o.term, -1 FROM oldtab o) x
      GROUP BY 1, 2, 3, 4 HAVING sum(x.d) <> 0;
  END IF;
  RETURN NULL;
END $fn$;
SELECT kernel.exempt_trigger('retrieval.postings_df_capture()', 'inserts the per-term change of the postings a statement wrote into retrieval.lexical_term_delta as the writer of the postings, in its transaction; reads nothing but its transition tables', 'security-review');
-- PostgreSQL takes a statement trigger with transition tables for one event only: three triggers. On the partitioned root they see
-- the rows of every leaf (the writers and the jobs name the root).
CREATE TRIGGER postings_df_insert AFTER INSERT ON retrieval.postings REFERENCING NEW TABLE AS newtab
  FOR EACH STATEMENT EXECUTE FUNCTION retrieval.postings_df_capture();
CREATE TRIGGER postings_df_delete AFTER DELETE ON retrieval.postings REFERENCING OLD TABLE AS oldtab
  FOR EACH STATEMENT EXECUTE FUNCTION retrieval.postings_df_capture();
CREATE TRIGGER postings_df_update AFTER UPDATE ON retrieval.postings REFERENCING OLD TABLE AS oldtab NEW TABLE AS newtab
  FOR EACH STATEMENT EXECUTE FUNCTION retrieval.postings_df_capture();

-- Every collection that has postings has a point count (folded, or a delta: the writer of a posting writes both in one transaction).
ALTER TABLE retrieval.collection_stats NO FORCE ROW LEVEL SECURITY;
ALTER TABLE retrieval.lexical_df_delta NO FORCE ROW LEVEL SECURITY;
ALTER TABLE retrieval.lexical_df_pending NO FORCE ROW LEVEL SECURITY;
INSERT INTO retrieval.lexical_df_pending (org_id, collection_id, space_id)
  SELECT s.org_id, s.collection_id, s.space_id FROM retrieval.collection_stats s
  UNION SELECT DISTINCT d.org_id, d.collection_id, d.space_id FROM retrieval.lexical_df_delta d WHERE d.term = -1;
ALTER TABLE retrieval.collection_stats FORCE ROW LEVEL SECURITY;
ALTER TABLE retrieval.lexical_df_delta FORCE ROW LEVEL SECURITY;
ALTER TABLE retrieval.lexical_df_pending FORCE ROW LEVEL SECURITY;
