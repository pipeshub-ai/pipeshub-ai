-- Schema for the PostgreSQL graph store (DATA_STORE=postgres): the nodes and edges the Neo4j and ArangoDB providers keep.
-- The owner of the SECURITY DEFINER functions of this schema (the vetted functions run after this file), normally created by the role bootstrap. The row
-- policy below names it, so it exists before it.
RESET ROLE;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pipeshub_vetted_graph') THEN
    CREATE ROLE pipeshub_vetted_graph NOLOGIN;
    GRANT pipeshub_vetted_graph TO pipeshub_owner WITH ADMIN TRUE, INHERIT FALSE, SET FALSE;
  END IF;
EXCEPTION WHEN insufficient_privilege THEN
  RAISE EXCEPTION 'the vetted owner of schema graph is missing and this role cannot create it: run the role bootstrap (python -m db.bootstrap_roles)';
END $$;
SET LOCAL ROLE pipeshub_owner;

CREATE SCHEMA graph;

-- One row per graph document. `coll` is the collection name callers use (records, recordGroups, users, apps, ...); the
-- document is stored as the callers hand it over (Arango form: `id` plus the fields). LH = LIST(org_id) with a HASH(org_id)
-- DEFAULT partition; a large org can later get a LIST partition of its own.
CREATE TABLE graph.nodes (
  org_id text NOT NULL,
  coll text NOT NULL,
  id text NOT NULL,
  doc jsonb NOT NULL,
  PRIMARY KEY (org_id, coll, id)
) PARTITION BY LIST (org_id);

-- One row per edge. `coll` is the edge collection (permission, belongsTo, nodeRelations, ...). An edge is identified by its collection and both
-- endpoints, collection included (two documents of different collections may share an id); from_id and to_id stay ahead of the collections, so a
-- lookup by id alone still uses the key. `ekey` separates parallel edges of one pair: a link keeps its relation type and a grant its type
-- (a user's own grant and the organization's to one record are two edges); empty otherwise.
CREATE TABLE graph.edges (
  org_id text NOT NULL,
  coll text NOT NULL,
  from_coll text NOT NULL,
  from_id text NOT NULL,
  to_coll text NOT NULL,
  to_id text NOT NULL,
  ekey text NOT NULL DEFAULT '',
  doc jsonb NOT NULL DEFAULT '{}',
  PRIMARY KEY (org_id, coll, from_id, from_coll, to_id, to_coll, ekey)
) PARTITION BY LIST (org_id);

DO $$
DECLARE i int; t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['nodes', 'edges'] LOOP
    EXECUTE format('CREATE TABLE graph.%1$s_def PARTITION OF graph.%1$s DEFAULT PARTITION BY HASH (org_id)', t);
    FOR i IN 0..15 LOOP
      EXECUTE format('CREATE TABLE graph.%1$s_h%3$s PARTITION OF graph.%1$s_def FOR VALUES WITH (MODULUS 16, REMAINDER %2$s)', t, i, lpad(i::text, 2, '0'));
    END LOOP;
  END LOOP;
END $$;

-- Autovacuum never analyzes a partitioned parent, only its leaves; a fresh load must not wait for the default 10 % change.
DO $$
DECLARE r regclass;
BEGIN
  FOR r IN SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
           WHERE n.nspname = 'graph' AND c.relkind = 'r' LOOP
    EXECUTE format('ALTER TABLE %s SET (autovacuum_analyze_scale_factor = 0.02, autovacuum_analyze_threshold = 200, autovacuum_vacuum_scale_factor = 0.05)', r);
  END LOOP;
END $$;

CREATE INDEX edges_by_target ON graph.edges (org_id, coll, to_id);
-- the lookups connectors make on every sync item
CREATE INDEX nodes_record_external ON graph.nodes (org_id, (doc->>'connectorId'), (doc->>'externalRecordId')) WHERE coll = 'records';
CREATE INDEX nodes_record_vrid ON graph.nodes (org_id, (doc->>'virtualRecordId')) WHERE coll = 'records';
CREATE INDEX nodes_record_parent ON graph.nodes (org_id, (doc->>'connectorId'), (doc->>'externalParentId')) WHERE coll = 'records';
CREATE INDEX nodes_user_userid ON graph.nodes (org_id, (doc->>'userId')) WHERE coll = 'users';
CREATE INDEX nodes_user_email ON graph.nodes (org_id, lower(doc->>'email')) WHERE coll = 'users';
CREATE INDEX nodes_group_external ON graph.nodes (org_id, (doc->>'connectorId'), (doc->>'externalGroupId')) WHERE coll = 'recordGroups';

-- p_jobs: the jobs role reads across orgs under a system context (org enumeration); it writes nothing here
SELECT kernel.apply_rls('graph.nodes', 'org', p_jobs => true, p_vetted => true);   -- the vetted reads of the org list and the connector instances cross orgs
SELECT kernel.apply_rls('graph.edges', 'org', p_jobs => true);

-- Connectors and indexing write the graph; the query service reads and writes it too (chat attachments and their permission edges, teams);
-- the node service never touches it.
GRANT USAGE ON SCHEMA graph TO pipeshub_connectors, pipeshub_indexing, pipeshub_query;
GRANT SELECT, INSERT, UPDATE, DELETE ON graph.nodes, graph.edges TO pipeshub_connectors, pipeshub_indexing, pipeshub_query;
GRANT USAGE ON SCHEMA graph TO pipeshub_jobs;
GRANT SELECT ON graph.nodes, graph.edges TO pipeshub_jobs;
