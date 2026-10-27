-- Test tables on top of the migrated kernel, secured through kernel.apply_rls like any real table:
-- two org-scoped tables (one with a jobs policy), a partitioned one for the plan checks, and a log every service can write.
CREATE EXTENSION IF NOT EXISTS pg_trgm SCHEMA ext;
SET ROLE pipeshub_owner;
CREATE SCHEMA kx;
CREATE TABLE kx.docs (org_id text NOT NULL, id text NOT NULL, title text, PRIMARY KEY (org_id, id));
CREATE TABLE kx.log (org_id text NOT NULL, note text);
CREATE TABLE kx.big (org_id text NOT NULL, id text NOT NULL, PRIMARY KEY (org_id, id)) PARTITION BY LIST (org_id);
CREATE TABLE kx.big_a PARTITION OF kx.big FOR VALUES IN ('aaaaaaaaaaaaaaaaaaaaaaaa');
CREATE TABLE kx.big_b PARTITION OF kx.big FOR VALUES IN ('bbbbbbbbbbbbbbbbbbbbbbbb');
CREATE TABLE kx.big_def PARTITION OF kx.big DEFAULT PARTITION BY HASH (org_id);
DO $$ BEGIN FOR i IN 0..15 LOOP
  EXECUTE format('CREATE TABLE kx.big_h%s PARTITION OF kx.big_def FOR VALUES WITH (MODULUS 16, REMAINDER %s)', i, i);
END LOOP; END $$;
SELECT kernel.apply_rls('kx.docs', 'org', p_jobs => true);
SELECT kernel.apply_rls('kx.log', 'org');
SELECT kernel.apply_rls('kx.big', 'org');
GRANT USAGE ON SCHEMA kx TO pipeshub_app, pipeshub_jobs;
-- the policy group holds no table privileges (the catalog check forbids it): the service roles do
GRANT SELECT, INSERT, UPDATE, DELETE ON kx.docs TO pipeshub_node, pipeshub_connectors, pipeshub_indexing, pipeshub_query, pipeshub_jobs;
GRANT SELECT, INSERT, UPDATE, DELETE ON kx.log, kx.big TO pipeshub_node, pipeshub_connectors, pipeshub_indexing, pipeshub_query;
RESET ROLE;
-- the superuser loads the rows (it bypasses RLS)
INSERT INTO kx.docs SELECT o, o || '-' || g, 't' FROM unnest(ARRAY[repeat('a',24),repeat('b',24),repeat('c',24),repeat('d',24)]) o, generate_series(1, 100) g;
INSERT INTO kx.big SELECT o, o || '-' || g FROM unnest(ARRAY[repeat('a',24),repeat('b',24),repeat('c',24)]) o, generate_series(1, 200) g;
ANALYZE kx.big;
