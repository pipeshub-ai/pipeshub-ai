-- Schema kg: the typed hot tables and the access data behind the PostgreSQL graph provider. graph.* stays the document store;
-- kg is written from it by vetted writers (a later revision), in the writer's own transaction.
-- The owner of the SECURITY DEFINER functions of this schema, normally created by the role bootstrap; the row policies below name it.
RESET ROLE;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pipeshub_vetted_kg') THEN
    CREATE ROLE pipeshub_vetted_kg NOLOGIN;
    GRANT pipeshub_vetted_kg TO pipeshub_owner WITH ADMIN TRUE, INHERIT FALSE, SET FALSE;
  END IF;
EXCEPTION WHEN insufficient_privilege THEN
  RAISE EXCEPTION 'the vetted owner of schema kg is missing and this role cannot create it: run the role bootstrap (python -m db.bootstrap_roles)';
END $$;
SET LOCAL ROLE pipeshub_owner;

CREATE SCHEMA kg;
GRANT USAGE ON SCHEMA kg TO pipeshub_connectors, pipeshub_indexing, pipeshub_query;

-- A surrogate key per graph node of an org: ids are text, kg joins on bigint.
CREATE SEQUENCE kg.node_sk_seq AS bigint;
-- The id of a class (kg.scope_classes): taken by the stamping module before it creates the class, never by a function.
CREATE SEQUENCE kg.class_id_seq AS bigint;

-- The advisory-lock class of the per-(org, app) stamping lock: every projection batch holds it (checks 93: transaction-scoped, a registered class).
INSERT INTO kernel.lock_keys VALUES (7360, 'kg_stamp', 'one lock per (org, app) for every write that projects graph.* into kg');

-- The kg tables: plain tables with org_id leading every primary key and unique key (so a later conversion to partitions by org never meets a key without it)
-- and the autovacuum options on each.
-- Written by the vetted writers only (no application role holds INSERT, UPDATE or DELETE on any of them; kernel check 110), read by
-- the services. Every table carries the per-org row policy for its vetted writer.
-- Not here yet (later revisions): the restamp queue and the scopes, record_state (written by the indexing role), the lookup and counter tables.

-- ---- the tables -------------------------------------------------------------------------------------------------------------------------
DO $$
DECLARE
  t record;
  opts constant text := 'autovacuum_analyze_scale_factor = 0.02, autovacuum_analyze_threshold = 200, autovacuum_vacuum_scale_factor = 0.05, autovacuum_vacuum_insert_scale_factor = 0.02';
BEGIN
  FOR t IN SELECT * FROM (VALUES
    ('node_keys', 'coll smallint NOT NULL, id text NOT NULL, sk bigint NOT NULL DEFAULT nextval(''kg.node_sk_seq''), PRIMARY KEY (org_id, coll, id), UNIQUE (org_id, sk)'),
    ('records', 'sk bigint NOT NULL, coll smallint NOT NULL, id text NOT NULL, kind smallint NOT NULL, app_sk bigint, group_sk bigint, parent_sk bigint, path bytea,
        rule text, model text, vrid text, name text, name_key text, updated_ms bigint, created_ms bigint, size_bytes bigint, record_type text, origin text, external_id text,
        deleted boolean NOT NULL DEFAULT false, placeholder boolean NOT NULL DEFAULT false, granted boolean NOT NULL DEFAULT false,
        class_id bigint, self_lit boolean, seed boolean, own_grant boolean, under_deleted boolean, legacy_reason smallint, leaf boolean,
        folder boolean NOT NULL DEFAULT false, hide_children boolean NOT NULL DEFAULT false, alien boolean NOT NULL DEFAULT false, PRIMARY KEY (org_id, sk)'),
    ('links', 'parent_sk bigint NOT NULL, kind smallint NOT NULL, child_sk bigint NOT NULL, PRIMARY KEY (org_id, parent_sk, kind, child_sk)'),
    ('scope_members', 'principal_sk bigint NOT NULL, node_sk bigint NOT NULL, gtype text NOT NULL, role text, app_sk bigint, leaf boolean NOT NULL DEFAULT false,
        PRIMARY KEY (org_id, principal_sk, node_sk, gtype)'),
    ('principal_closure', 'user_sk bigint NOT NULL, principal_sk bigint NOT NULL, app_sk bigint NOT NULL, via text NOT NULL, PRIMARY KEY (org_id, user_sk, principal_sk, app_sk)'),
    ('scope_classes', 'id bigint NOT NULL, app_sk bigint NOT NULL, group_sk bigint, clauses jsonb NOT NULL, hash bytea NOT NULL, PRIMARY KEY (org_id, id)'),
    ('class_literals', 'anchor_sk bigint NOT NULL, class_id bigint NOT NULL, PRIMARY KEY (org_id, anchor_sk, class_id)'),
    ('graph_changes', 'id bigint GENERATED ALWAYS AS IDENTITY, txid xid8 NOT NULL DEFAULT pg_current_xact_id(), app_id text NOT NULL DEFAULT '''', ends text[] NOT NULL CHECK (cardinality(ends) > 0),
        PRIMARY KEY (org_id, id)'),
    ('projection_state', 'app_sk bigint NOT NULL, code_epoch int NOT NULL DEFAULT 0, verified_at timestamptz, mode text NOT NULL DEFAULT ''legacy'' CHECK (mode IN (''legacy'', ''shadow'', ''enforce'')),
        verify_digest text, backfill_cursor text,
        opens boolean NOT NULL DEFAULT false, has_group_level boolean NOT NULL DEFAULT false, facts_set boolean NOT NULL DEFAULT false,
        cross_app boolean NOT NULL DEFAULT false, PRIMARY KEY (org_id, app_sk)')
  ) v(name, cols) LOOP
    EXECUTE format('CREATE TABLE kg.%I (org_id text NOT NULL, %s)', t.name, t.cols);
    EXECUTE format('ALTER TABLE kg.%I SET (%s)', t.name, opts);
  END LOOP;
END $$;

-- ---- row-level security and grants ------------------------------------------------------------------------------------------------------
-- No vetted_read: the vetted writer's policy is the org predicate and covers its reads as well. No jobs access: restamp and backfill run as the connectors service.
SELECT kernel.apply_rls('kg.node_keys', 'org', p_vetted_write => true);
SELECT kernel.apply_rls('kg.records', 'org', p_vetted_write => true);
SELECT kernel.apply_rls('kg.links', 'org', p_vetted_write => true);
SELECT kernel.apply_rls('kg.scope_members', 'org', p_vetted_write => true);
SELECT kernel.apply_rls('kg.principal_closure', 'org', p_vetted_write => true);
SELECT kernel.apply_rls('kg.scope_classes', 'org', p_vetted_write => true);
SELECT kernel.apply_rls('kg.class_literals', 'org', p_vetted_write => true);
SELECT kernel.apply_rls('kg.graph_changes', 'org', p_vetted_write => true);
SELECT kernel.apply_rls('kg.projection_state', 'org', p_vetted_write => true);
SELECT kernel.require_vetted_org_policy('kg.node_keys');
SELECT kernel.require_vetted_org_policy('kg.records');
SELECT kernel.require_vetted_org_policy('kg.links');
SELECT kernel.require_vetted_org_policy('kg.scope_members');
SELECT kernel.require_vetted_org_policy('kg.principal_closure');
SELECT kernel.require_vetted_org_policy('kg.scope_classes');
SELECT kernel.require_vetted_org_policy('kg.class_literals');
SELECT kernel.require_vetted_org_policy('kg.graph_changes');
SELECT kernel.require_vetted_org_policy('kg.projection_state');

GRANT SELECT ON kg.node_keys, kg.records, kg.links, kg.scope_members, kg.principal_closure, kg.scope_classes, kg.class_literals,
  kg.graph_changes, kg.projection_state TO pipeshub_connectors, pipeshub_indexing, pipeshub_query;
GRANT USAGE ON SEQUENCE kg.node_sk_seq TO pipeshub_vetted_kg;
GRANT USAGE ON SEQUENCE kg.class_id_seq TO pipeshub_connectors;
