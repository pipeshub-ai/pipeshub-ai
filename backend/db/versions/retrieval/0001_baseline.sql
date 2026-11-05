-- Vector retrieval, created whole: the global catalog first, then the org-scoped tables, placement, postings and space checks. The vetted
-- functions are run by the revision after this file.
RESET ROLE;
-- The owner of the SECURITY DEFINER functions of this schema is normally created by the role bootstrap (python -m db.bootstrap_roles);
-- it is created here, NOLOGIN, only when missing (a bare database, or tests).
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pipeshub_vetted_retrieval') THEN
    CREATE ROLE pipeshub_vetted_retrieval NOLOGIN;
    GRANT pipeshub_vetted_retrieval TO pipeshub_owner WITH ADMIN TRUE, INHERIT FALSE, SET FALSE;
  END IF;
EXCEPTION WHEN insufficient_privilege THEN
  RAISE EXCEPTION 'the vetted owner of schema retrieval is missing and this role cannot create it: run the role bootstrap (python -m db.bootstrap_roles --vetted retrieval)';
END $$;
-- pgvector needs a superuser to install, so the administrator installs it (PG_BOOTSTRAP_EXTENSIONS=vector in the role
-- bootstrap), into schema ext. A migration run by a superuser (a development database) installs it itself.
-- The halfvec type and the bit HNSW operator class need 0.7.0 or later.
DO $$
DECLARE v text; n name;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector') AND EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'ext') THEN
    BEGIN
      CREATE EXTENSION vector WITH SCHEMA ext;
    EXCEPTION WHEN insufficient_privilege OR undefined_file OR feature_not_supported THEN NULL;       -- the check below says what to do
    END;
  END IF;
  SELECT e.extversion, s.nspname INTO v, n FROM pg_extension e JOIN pg_namespace s ON s.oid = e.extnamespace WHERE e.extname = 'vector';
  IF v IS NULL THEN
    RAISE EXCEPTION 'the vector extension is not installed: as an administrator run CREATE EXTENSION vector WITH SCHEMA ext (or set PG_BOOTSTRAP_EXTENSIONS=vector,pg_trgm for db.bootstrap_roles)';
  ELSIF n <> 'ext' THEN
    RAISE EXCEPTION 'the vector extension lives in schema %, it must be in schema ext: DROP and re-create it WITH SCHEMA ext', n;
  ELSIF string_to_array(v, '.')::int[] < ARRAY[0, 7, 0] THEN
    RAISE EXCEPTION 'pgvector % is too old: 0.7.0 or later is required', v;
  END IF;
END $$;
SET LOCAL ROLE pipeshub_owner;

CREATE SCHEMA retrieval;
GRANT USAGE ON SCHEMA retrieval TO pipeshub_node, pipeshub_connectors, pipeshub_indexing, pipeshub_query, pipeshub_jobs;
GRANT USAGE ON SCHEMA ext TO pipeshub_jobs;            -- the copy and repair jobs write vectors (the service roles reach ext through the policy group)

-- Global class: no org-scoped data and no application privilege. Application roles reach them through the vetted catalog
-- functions only (read for every service; every write is a jobs path, so a compromised service cannot rewrite the catalog).
CREATE TABLE retrieval.embedding_spaces (
  space_id smallint PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
  model    text NOT NULL,
  dim      int  NOT NULL,
  metric   text NOT NULL CHECK (metric IN ('cosine', 'dot', 'l2')),
  status   text NOT NULL DEFAULT 'building' CHECK (status IN ('building', 'ready')),
  UNIQUE (model, dim, metric),
  -- A row must fit one page (8,160 bytes) and a vector takes 2 bytes a dimension: 3,840 leaves room and covers every model up to 3,584 dimensions.
  CONSTRAINT embedding_spaces_dim_check CHECK (dim BETWEEN 1 AND 3840));

CREATE TABLE retrieval.collections (
  collection_id   smallint PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
  name            text NOT NULL,
  sparse_enabled  boolean NOT NULL,
  sparse_idf      boolean NOT NULL,      -- no space here: the space is per org
  lexical_version text NOT NULL,         -- fastembed version + stopword sha + k1/b/avg_len (set by the sparse leg)
  status          text NOT NULL DEFAULT 'ready' CHECK (status IN ('ready', 'building', 'deleting')),
  created_at      timestamptz NOT NULL DEFAULT now(),
  stamp           text);                 -- fingerprint the entity index compares to know it projected into this collection; set once by the vetted function
CREATE UNIQUE INDEX collections_name_live ON retrieval.collections (name) WHERE status <> 'deleting';   -- recreate while the old rows drain

SELECT kernel.apply_rls('retrieval.embedding_spaces', 'global', p_vetted => true, p_vetted_write => true);
SELECT kernel.apply_rls('retrieval.collections', 'global', p_vetted => true, p_vetted_write => true);

-- The collections of the default strategy exist from the start, so a fresh install needs no catalog write at run time.
INSERT INTO retrieval.collections (name, sparse_enabled, sparse_idf, lexical_version)
  VALUES ('records', true, true, 'unset'), ('entities', true, true, 'unset');

-- Org-scoped tables. Every key leads with org_id. The big tables are partitioned LIST (org_id): an org that was
-- given its own leaf (made by the maintenance jobs) has its own indexes and drops in O(1); every other org lives in the DEFAULT
-- partition, which is hash-partitioned by org_id, and is searched as a filtered set of that leaf.
-- Vector indexes are NOT created here: they depend on the embedding dimension, which belongs to a space; the maintenance
-- jobs create them per leaf, concurrently.


-- Which embedding space each org searches and writes (the spaces themselves are global).
CREATE TABLE retrieval.org_spaces (
  org_id   text NOT NULL,
  space_id smallint NOT NULL,
  dim      smallint NOT NULL,          -- the space's dimension, copied from the descriptor by the functions that write this table
  role     text NOT NULL CHECK (role IN ('active', 'shadow', 'retiring')),
  since    timestamptz NOT NULL DEFAULT now(),
  named_by text,                -- which model first wrote to a nameless space (see the vetted function org_space_named_by)
  PRIMARY KEY (org_id, space_id),
  UNIQUE (org_id, space_id, dim));    -- what points reference: a point's vector length must be its space's dimension
CREATE UNIQUE INDEX org_spaces_one_active ON retrieval.org_spaces (org_id) WHERE role = 'active';

-- Short columns and the binary-quantised vector stay in the row (STORAGE MAIN; a 1024-dimension halfvec pushes a row past the 2 KB TOAST target and
-- the default would move the other columns out of line, which made a bit scan 3x slower); lex_terms is only read to remove a point's postings.
-- New leaves copy these settings (CREATE TABLE ... LIKE ... INCLUDING ALL).
CREATE TABLE retrieval.points (               -- narrow: only what search and rerank read
  org_id        text STORAGE MAIN NOT NULL,
  point_sk      bigint NOT NULL GENERATED ALWAYS AS IDENTITY,
  collection_id smallint NOT NULL,
  space_id      smallint NOT NULL,            -- immutable; the partial-index key
  point_id      uuid NOT NULL,
  group_key     text STORAGE MAIN NOT NULL,                -- vrid for records, point_id::text for entities
  vrid text STORAGE MAIN, block_id text STORAGE MAIN, ent_type text STORAGE MAIN, ent_id text STORAGE MAIN, ent_kind text STORAGE MAIN, ent_level text STORAGE MAIN,
  embedding     ext.halfvec STORAGE PLAIN NOT NULL,
  embedding_dim smallint NOT NULL,
  bq            bit varying STORAGE MAIN GENERATED ALWAYS AS (ext.binary_quantize(embedding)) STORED,
  lex_terms     bigint[] STORAGE EXTERNAL,
  CHECK (ext.vector_dims(embedding) = embedding_dim),
  FOREIGN KEY (org_id, space_id, embedding_dim) REFERENCES retrieval.org_spaces (org_id, space_id, dim),   -- the database refuses a mismatch
  PRIMARY KEY (org_id, point_sk),
  UNIQUE (org_id, collection_id, space_id, point_id)) PARTITION BY LIST (org_id);
CREATE INDEX points_group ON retrieval.points (org_id, collection_id, group_key);
CREATE INDEX points_block ON retrieval.points (org_id, collection_id, vrid, block_id);
CREATE INDEX points_entity ON retrieval.points (org_id, collection_id, ent_type, ent_id) WHERE ent_type IS NOT NULL;

CREATE TABLE retrieval.point_text (
  org_id       text NOT NULL,
  point_sk     bigint NOT NULL,
  page_content text,
  payload      jsonb NOT NULL,                -- everything but page_content and the membership arrays
  PRIMARY KEY (org_id, point_sk)) PARTITION BY LIST (org_id);

-- Membership is per group (one row per vrid, not per chunk). computed_at is the compare-and-set fence.
CREATE TABLE retrieval.point_groups (
  org_id                 text NOT NULL,
  collection_id          smallint NOT NULL,
  group_key              text NOT NULL,
  connector_ids          text[] NOT NULL DEFAULT '{}',
  record_group_ids       text[] NOT NULL DEFAULT '{}',
  root_record_group_ids  text[] NOT NULL DEFAULT '{}',
  computed_at            timestamptz NOT NULL,
  PRIMARY KEY (org_id, collection_id, group_key)) PARTITION BY LIST (org_id);

CREATE TABLE retrieval.group_members (
  org_id        text NOT NULL,
  collection_id smallint NOT NULL,
  group_key     text NOT NULL,
  kind          smallint NOT NULL CHECK (kind IN (1, 2, 3)),     -- 1 connector, 2 record group, 3 root record group
  member_id     text NOT NULL,
  PRIMARY KEY (org_id, collection_id, group_key, kind, member_id)) PARTITION BY LIST (org_id);
CREATE INDEX group_members_member ON retrieval.group_members (org_id, collection_id, kind, member_id, group_key);

-- Only written while HNSW safe mode is on (pgvector before 0.8.4): a delete is a tombstone, the nightly job deletes for real.
CREATE TABLE retrieval.point_tombstones (
  org_id     text NOT NULL,
  point_sk   bigint NOT NULL,
  deleted_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (org_id, point_sk)) PARTITION BY LIST (org_id);

CREATE TABLE retrieval.lexical_df_delta (
  org_id text NOT NULL, collection_id smallint NOT NULL, space_id smallint NOT NULL, term bigint NOT NULL, d int NOT NULL,
  seq bigint NOT NULL GENERATED ALWAYS AS IDENTITY,
  PRIMARY KEY (org_id, seq)) PARTITION BY LIST (org_id);

-- The search sums the term -1 rows (the point count) of one collection and space: an index of just those rows keeps that read off the
-- other deltas of the org.
CREATE INDEX lexical_df_delta_count ON retrieval.lexical_df_delta (org_id, collection_id, space_id) INCLUDE (d) WHERE term = -1;

CREATE TABLE retrieval.collection_stats (
  org_id text NOT NULL, collection_id smallint NOT NULL, space_id smallint NOT NULL, n_points bigint NOT NULL,
  PRIMARY KEY (org_id, collection_id, space_id));
CREATE TABLE retrieval.member_stats (
  org_id text NOT NULL, collection_id smallint NOT NULL, space_id smallint NOT NULL, kind smallint NOT NULL, member_id text NOT NULL,
  n_points bigint NOT NULL,
  PRIMARY KEY (org_id, collection_id, space_id, kind, member_id));

-- The default tree: every org without its own leaf. The hash modulus is a tuning choice (8 for now).
DO $$
DECLARE t text; i int;
BEGIN
  FOREACH t IN ARRAY ARRAY['points', 'point_text', 'point_groups', 'group_members', 'point_tombstones', 'lexical_df_delta'] LOOP
    EXECUTE format('CREATE TABLE retrieval.%I PARTITION OF retrieval.%I DEFAULT PARTITION BY HASH (org_id)', t || '_default', t);
    FOR i IN 0..7 LOOP
      EXECUTE format('CREATE TABLE retrieval.%I PARTITION OF retrieval.%I FOR VALUES WITH (MODULUS 8, REMAINDER %s)', t || '_h' || i, t || '_default', i);
    END LOOP;
  END LOOP;
END $$;

-- Row-level security on every table and partition; jobs (purge, fold, GC, promotion) and the vetted functions read and write all orgs
-- through their own policies.
SELECT kernel.apply_rls('retrieval.org_spaces', 'org', p_jobs => true, p_vetted => true, p_vetted_write => true);
SELECT kernel.apply_rls('retrieval.points', 'org', p_jobs => true, p_vetted => true);
SELECT kernel.apply_rls('retrieval.point_text', 'org', p_jobs => true, p_vetted => true);
SELECT kernel.apply_rls('retrieval.point_groups', 'org', p_jobs => true, p_vetted => true);
SELECT kernel.apply_rls('retrieval.group_members', 'org', p_jobs => true, p_vetted => true);
SELECT kernel.apply_rls('retrieval.point_tombstones', 'org', p_jobs => true, p_vetted => true);
SELECT kernel.apply_rls('retrieval.lexical_df_delta', 'org', p_jobs => true, p_vetted => true);
SELECT kernel.apply_rls('retrieval.collection_stats', 'org', p_jobs => true, p_vetted => true);
SELECT kernel.apply_rls('retrieval.member_stats', 'org', p_jobs => true, p_vetted => true);

-- Grants are explicit per table. Writers: the indexing service; the query service only reads. The connectors process reaches the vector store
-- only for its health check (collection list through the vetted read functions), so it has no privilege on these tables.
GRANT SELECT, INSERT, UPDATE, DELETE ON retrieval.points, retrieval.point_text, retrieval.point_groups, retrieval.group_members TO pipeshub_indexing;
GRANT SELECT ON retrieval.points, retrieval.point_text, retrieval.point_groups, retrieval.group_members, retrieval.point_tombstones,
  retrieval.collection_stats, retrieval.member_stats TO pipeshub_query;
GRANT SELECT, INSERT, DELETE ON retrieval.point_tombstones TO pipeshub_indexing;   -- an upsert revives its point
GRANT SELECT ON retrieval.collection_stats, retrieval.member_stats TO pipeshub_indexing;
GRANT INSERT ON retrieval.lexical_df_delta TO pipeshub_indexing;
GRANT SELECT, INSERT, UPDATE, DELETE ON retrieval.points, retrieval.point_text, retrieval.point_groups, retrieval.group_members,
  retrieval.point_tombstones, retrieval.lexical_df_delta, retrieval.collection_stats, retrieval.member_stats,
  retrieval.org_spaces TO pipeshub_jobs;

-- The default placement gives every org a LIST leaf of its own BEFORE its first vector write, so its rows never enter the shared
-- default tree and are never moved. Creating a leaf is DDL, which no application role may run, so the writing service records the
-- request (retrieval.org_leaf_request) and waits for the placement worker (a maintenance job) to create it
-- (retrieval.org_leaf_status). One row per org: asking twice is harmless. A request is pending until it is placed (done_at) or
-- closed as failed (failed_at, with the reason in note): the worker refuses an org that already has rows in the shared tree (that
-- needs the offline promotion) and gives up after a bounded number of attempts, backing off between them. A new ask re-opens a
-- failed request at most every 30 seconds, so a fixed problem is picked up and a broken one cannot occupy the worker.
CREATE TABLE retrieval.placement_requests (
  org_id       text NOT NULL PRIMARY KEY,
  requested_at timestamptz NOT NULL DEFAULT now(),
  done_at      timestamptz,
  failed_at    timestamptz,
  note         text,
  attempts     int NOT NULL DEFAULT 0,
  next_try_at  timestamptz);
SELECT kernel.apply_rls('retrieval.placement_requests', 'org', p_jobs => true, p_vetted => true, p_vetted_write => true);
GRANT SELECT, UPDATE ON retrieval.placement_requests TO pipeshub_jobs;

-- The lexical leg reads postings: one row per (point, term) with the BM25 weight, in a term-major index, so a query scans only the
-- postings of its own terms (no per-point work, exact for any number of terms). A term's document frequency is the number of its
-- postings, so it cannot drift from the points; only the point count is kept as a counter (lexical_df_delta, term -1).
-- Term ids are the 32-bit ids of the sparse model, stored as signed integers (ids above 2^31 - 1 wrap by 2^32; the search wraps its
-- query terms the same way). points.lex_terms is kept (the terms to remove when a point is replaced or deleted); the weights live
-- in the postings only.
CREATE TABLE retrieval.postings (
  org_id        text NOT NULL,
  collection_id smallint NOT NULL,
  space_id      smallint NOT NULL,
  term          integer NOT NULL,
  point_sk      bigint NOT NULL,
  w             real NOT NULL,
  PRIMARY KEY (org_id, collection_id, space_id, term, point_sk) INCLUDE (w)) PARTITION BY LIST (org_id);

-- The search reads postings with index-only scans, which are fast only for pages the visibility map marks all-visible. Postings are
-- insert-mostly, so the insert-triggered autovacuum must come early on every leaf (the default waits for 20 % of the rows).
-- The code that creates leaves sets the same options on them.
DO $$
DECLARE i int;
BEGIN
  CREATE TABLE retrieval.postings_default PARTITION OF retrieval.postings DEFAULT PARTITION BY HASH (org_id);
  FOR i IN 0..7 LOOP
    EXECUTE format('CREATE TABLE retrieval.%I PARTITION OF retrieval.postings_default FOR VALUES WITH (MODULUS 8, REMAINDER %s) '
                   'WITH (autovacuum_vacuum_insert_scale_factor = 0.02, autovacuum_vacuum_insert_threshold = 2000)', 'postings_h' || i, i);
  END LOOP;
END $$;

SELECT kernel.apply_rls('retrieval.postings', 'org', p_jobs => true, p_vetted => true);
GRANT SELECT, INSERT, UPDATE, DELETE ON retrieval.postings TO pipeshub_indexing, pipeshub_jobs;

-- The result of the bit-quantisation check of an embedding space (bit HNSW + rerank against exact search), kept per org and space so
-- that a restart or another pod does not repeat a scan of the org: the verdict (recall) with when it was taken, and, after a check
-- that could not finish (no slot, timeout), when to try again. The services write their own org's row; RLS pins it.
CREATE TABLE retrieval.space_checks (
  org_id      text NOT NULL,
  space_id    smallint NOT NULL,
  recall      real,                       -- mean recall of the last finished check; NULL until one finishes
  samples     int,
  checked_at  timestamptz,
  attempts    int NOT NULL DEFAULT 0,     -- failed attempts since the last finished check
  next_try_at timestamptz,
  PRIMARY KEY (org_id, space_id),
  FOREIGN KEY (org_id, space_id) REFERENCES retrieval.org_spaces (org_id, space_id) ON DELETE CASCADE);
SELECT kernel.apply_rls('retrieval.space_checks', 'org', p_jobs => true, p_vetted => true);
GRANT SELECT, INSERT, UPDATE ON retrieval.space_checks TO pipeshub_query, pipeshub_indexing;
GRANT SELECT, INSERT, UPDATE, DELETE ON retrieval.space_checks TO pipeshub_jobs;

-- Advisory-lock classes taken by retrieval code (the admission slots of a search) and by the maintenance jobs.
INSERT INTO kernel.lock_keys VALUES
  (7361, 'retrieval_fold', 'one fold or recount of the point count per org'),
  (7362, 'retrieval_group_gc', 'one group sweep per org'),
  (7363, 'retrieval_purge', 'one tombstone purge per org'),
  (7370, 'retrieval_admission', 'slots that cap concurrent heavy vector statements per database instance (key: slot kind, slot number)');
