-- Schema kg: the typed hot tables and the access data behind the PostgreSQL graph provider. graph.* stays the document store;
-- kg is written from it by the vetted writers below, in the writer's own transaction.
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
GRANT USAGE ON SCHEMA kg TO pipeshub_connectors, pipeshub_indexing, pipeshub_query, pipeshub_jobs;

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

-- ---- helpers of the change capture ---------------------------------------------------------------------------------------------------
-- The collections kg projects, as the small code graph_changes stores: apps, record groups and records are nodes of kg.records; users, groups,
-- roles, teams and organizations are only keyed (they hold and receive grants and memberships).
CREATE FUNCTION kg.coll_code(p_coll text) RETURNS smallint LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT CASE p_coll WHEN 'apps' THEN 1 WHEN 'recordGroups' THEN 2 WHEN 'records' THEN 3 WHEN 'users' THEN 4 WHEN 'groups' THEN 5
                     WHEN 'roles' THEN 6 WHEN 'teams' THEN 7 WHEN 'organizations' THEN 8 END::smallint $$;

-- These four SQL helpers set no search_path on purpose: a function with a SET clause is never inlined, and the trigger would call each once per row. They name no object, only literals
-- and pg_catalog operators, and the trigger that calls them sets its own search path.
-- Which end of an edge row a change to it changes in kg, and which part of its document the projection reads. A hierarchy link (parent to child) changes the
-- child's row; a belongs-to and an inherits-permissions link, the source's; a grant, the node it is on (a record, record group or app) or, for a membership of a
-- group, role or team, the member (its closure); a user-app link, the app; an authenticated-as link, the user. The other end is untouched, so a connector with
-- a record group of a million members does not re-project the group for each of them.
CREATE FUNCTION kg.edge_ends(p_coll text, p_from_coll text, p_from_id text, p_to_coll text, p_to_id text, p_doc jsonb) RETURNS TABLE (coll text, id text)
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT x.c, x.i FROM (VALUES (p_from_coll, p_from_id, 'f'), (p_to_coll, p_to_id, 't')) x(c, i, side)
  WHERE kg.coll_code(x.c) IS NOT NULL AND CASE p_coll
    WHEN 'nodeRelations' THEN COALESCE(p_doc->>'relationshipType' IN ('PARENT_CHILD', 'ATTACHMENT'), false) AND x.side = 't'
    WHEN 'belongsTo' THEN x.side = 'f'
    WHEN 'inheritPermissions' THEN x.side = 'f'
    WHEN 'permission' THEN CASE WHEN p_to_coll IN ('apps', 'recordGroups', 'records') THEN x.side = 't' WHEN p_to_coll IN ('groups', 'roles', 'teams') THEN x.side = 'f' ELSE true END
    WHEN 'userAppRelation' THEN x.side = 't'
    WHEN 'authenticatedAs' THEN x.side = 'f'
    ELSE false END $$;

CREATE FUNCTION kg.edge_sem(p_coll text, p_doc jsonb) RETURNS jsonb LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT CASE p_coll WHEN 'permission' THEN jsonb_build_object('type', p_doc->'type', 'role', p_doc->'role')
                     WHEN 'nodeRelations' THEN jsonb_build_object('relationshipType', p_doc->'relationshipType')
                     WHEN 'authenticatedAs' THEN jsonb_build_object('connectorId', p_doc->'connectorId')
                     ELSE '{}'::jsonb END $$;

-- What the projection reads of a node document. A node update that leaves this unchanged (an indexing-status flip, a generation bump) is not a change.
CREATE FUNCTION kg.proj_doc(p_coll text, p_doc jsonb) RETURNS jsonb LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT CASE WHEN p_coll IN ('apps', 'recordGroups', 'records') THEN jsonb_strip_nulls(jsonb_build_object(
    'accessRule', p_doc->'accessRule', 'permissionModel', p_doc->'permissionModel',
    'connectorId', p_doc->'connectorId', 'type', p_doc->'type', 'name', p_doc->'name', 'groupName', p_doc->'groupName', 'recordName', p_doc->'recordName',
    'externalRecordId', p_doc->'externalRecordId', 'recordType', p_doc->'recordType', 'origin', p_doc->'origin', 'virtualRecordId', p_doc->'virtualRecordId',
    'createdAtTimestamp', p_doc->'createdAtTimestamp', 'updatedAtTimestamp', p_doc->'updatedAtTimestamp',
    'sourceCreatedAtTimestamp', p_doc->'sourceCreatedAtTimestamp', 'sourceLastModifiedTimestamp', p_doc->'sourceLastModifiedTimestamp',
    'sizeInBytes', p_doc->'sizeInBytes', 'mimeType', p_doc->'mimeType', 'hideChildren', p_doc->'hideChildren', 'orgId', p_doc->'orgId'))
    || COALESCE(CASE WHEN jsonb_typeof(p_doc->'isDeleted') <> 'null' THEN jsonb_build_object('isDeleted', p_doc->'isDeleted') END, '{}'::jsonb)
    || COALESCE(CASE WHEN jsonb_typeof(p_doc->'isPlaceholder') <> 'null' THEN jsonb_build_object('isPlaceholder', p_doc->'isPlaceholder') END, '{}'::jsonb) END $$;

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
    ('graph_changes', 'id bigint GENERATED ALWAYS AS IDENTITY, txid xid8 NOT NULL DEFAULT pg_current_xact_id(), app_id text NOT NULL DEFAULT '''', ends text[] NOT NULL,
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
SELECT kernel.require_vetted_org_policy('kg.graph_changes', ARRAY['insert']);       -- the capture trigger inserts as the role that wrote the graph
SELECT kernel.require_vetted_org_policy('kg.projection_state');

GRANT SELECT ON kg.node_keys, kg.records, kg.links, kg.scope_members, kg.principal_closure, kg.scope_classes, kg.class_literals,
  kg.graph_changes, kg.projection_state TO pipeshub_connectors, pipeshub_indexing, pipeshub_query, pipeshub_jobs;
-- the capture trigger runs as the role that writes graph.nodes / graph.edges; it only inserts (and never reads) kg.graph_changes
GRANT INSERT ON kg.graph_changes TO pipeshub_connectors, pipeshub_indexing, pipeshub_query;
GRANT USAGE ON SEQUENCE kg.node_sk_seq TO pipeshub_vetted_kg;
GRANT USAGE ON SEQUENCE kg.class_id_seq TO pipeshub_connectors;

-- ---- change capture: nothing written to graph.nodes or graph.edges is missed ------------------------------------------------------------------
-- One row per statement and connector in kg.graph_changes, written by the statement-level triggers below as the role that made the change, in its transaction: the
-- keys of the changed nodes (and of the ends of a changed edge that its rows depend on, kg.edge_ends) as an array of 'code:id' strings. A writer pays for a handful
-- of rows per transaction, not one per node. A projection (the inline flush of the connectors service, or the drainer) re-projects the named nodes from the current graph
-- and deletes the rows (kg.w_changes_ack / kg.w_changes_delete); until then a reader of that connector takes today's walk.
-- app_id names the connector of a node row ('' for an edge row, and for the whole org) so the trigger needs no read of kg or of graph: the restore role has neither.
-- shortcut: one statement writes one array per org and connector, however many rows it changes (a restore of many millions of rows is one array), until a bulk loader needs chunks.
-- PostgreSQL rejects a statement trigger with transition tables on several events and a WHEN that reads OLD/NEW, so: three triggers per table and the filters here.
CREATE FUNCTION kg.log_graph_change() RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, kg, pg_temp AS $fn$
BEGIN
  IF TG_TABLE_NAME = 'nodes' THEN
    IF TG_OP = 'INSERT' THEN
      INSERT INTO kg.graph_changes (org_id, app_id, ends)
        SELECT n.org_id, CASE n.coll WHEN 'apps' THEN n.id ELSE COALESCE(n.doc->>'connectorId', '') END, array_agg(kg.coll_code(n.coll) || ':' || n.id)
        FROM newtab n WHERE kg.coll_code(n.coll) IS NOT NULL GROUP BY 1, 2;
    ELSIF TG_OP = 'DELETE' THEN
      INSERT INTO kg.graph_changes (org_id, app_id, ends)
        SELECT o.org_id, CASE o.coll WHEN 'apps' THEN o.id ELSE COALESCE(o.doc->>'connectorId', '') END, array_agg(kg.coll_code(o.coll) || ':' || o.id)
        FROM oldtab o WHERE kg.coll_code(o.coll) IS NOT NULL GROUP BY 1, 2;
    ELSE
      INSERT INTO kg.graph_changes (org_id, app_id, ends)
        SELECT n.org_id, CASE n.coll WHEN 'apps' THEN n.id ELSE COALESCE(n.doc->>'connectorId', '') END, array_agg(kg.coll_code(n.coll) || ':' || n.id)
        FROM newtab n JOIN oldtab o ON o.org_id = n.org_id AND o.coll = n.coll AND o.id = n.id
        WHERE kg.coll_code(n.coll) IS NOT NULL AND kg.proj_doc(n.coll, o.doc) IS DISTINCT FROM kg.proj_doc(n.coll, n.doc) GROUP BY 1, 2;
      -- a node that changed connector is also logged under the connector it left, so that connector is not current while its record is stale
      INSERT INTO kg.graph_changes (org_id, app_id, ends)
        SELECT o.org_id, CASE o.coll WHEN 'apps' THEN o.id ELSE COALESCE(o.doc->>'connectorId', '') END, array_agg(kg.coll_code(o.coll) || ':' || o.id)
        FROM newtab n JOIN oldtab o ON o.org_id = n.org_id AND o.coll = n.coll AND o.id = n.id
        WHERE kg.coll_code(o.coll) IS NOT NULL AND o.coll <> 'apps' AND COALESCE(o.doc->>'connectorId', '') IS DISTINCT FROM COALESCE(n.doc->>'connectorId', '') GROUP BY 1, 2;
      -- an UPDATE of the key columns: the old key is a node that went, the new one a node that came
      INSERT INTO kg.graph_changes (org_id, app_id, ends)
        SELECT o.org_id, CASE o.coll WHEN 'apps' THEN o.id ELSE COALESCE(o.doc->>'connectorId', '') END, array_agg(kg.coll_code(o.coll) || ':' || o.id)
        FROM oldtab o WHERE kg.coll_code(o.coll) IS NOT NULL AND NOT EXISTS (SELECT 1 FROM newtab n WHERE n.org_id = o.org_id AND n.coll = o.coll AND n.id = o.id) GROUP BY 1, 2;
      INSERT INTO kg.graph_changes (org_id, app_id, ends)
        SELECT n.org_id, CASE n.coll WHEN 'apps' THEN n.id ELSE COALESCE(n.doc->>'connectorId', '') END, array_agg(kg.coll_code(n.coll) || ':' || n.id)
        FROM newtab n WHERE kg.coll_code(n.coll) IS NOT NULL AND NOT EXISTS (SELECT 1 FROM oldtab o WHERE o.org_id = n.org_id AND o.coll = n.coll AND o.id = n.id) GROUP BY 1, 2;
    END IF;
  ELSE
    IF TG_OP = 'INSERT' THEN
      INSERT INTO kg.graph_changes (org_id, ends)
        SELECT e.org_id, array_agg(DISTINCT kg.coll_code(x.coll) || ':' || x.id) FROM newtab e, LATERAL kg.edge_ends(e.coll, e.from_coll, e.from_id, e.to_coll, e.to_id, e.doc) x GROUP BY 1;
    ELSIF TG_OP = 'DELETE' THEN
      INSERT INTO kg.graph_changes (org_id, ends)
        SELECT e.org_id, array_agg(DISTINCT kg.coll_code(x.coll) || ':' || x.id) FROM oldtab e, LATERAL kg.edge_ends(e.coll, e.from_coll, e.from_id, e.to_coll, e.to_id, e.doc) x GROUP BY 1;
    ELSE   -- a changed document: only when what the projection reads of it changed (the old and the new row name the nodes: a link can be retyped to or from a hierarchy relation)
      INSERT INTO kg.graph_changes (org_id, ends)
        SELECT e.org_id, array_agg(DISTINCT kg.coll_code(x.coll) || ':' || x.id)
        FROM newtab e JOIN oldtab o ON o.org_id = e.org_id AND o.coll = e.coll AND o.from_coll = e.from_coll AND o.from_id = e.from_id
                                   AND o.to_coll = e.to_coll AND o.to_id = e.to_id AND o.ekey = e.ekey,
             LATERAL (SELECT * FROM kg.edge_ends(e.coll, e.from_coll, e.from_id, e.to_coll, e.to_id, e.doc)
                      UNION SELECT * FROM kg.edge_ends(o.coll, o.from_coll, o.from_id, o.to_coll, o.to_id, o.doc)) x
        WHERE kg.edge_sem(e.coll, o.doc) IS DISTINCT FROM kg.edge_sem(e.coll, e.doc) GROUP BY 1;
      -- an UPDATE of the key columns (from_id, to_id ...): the ends of the edge that went and of the edge that came
      INSERT INTO kg.graph_changes (org_id, ends)
        SELECT o.org_id, array_agg(DISTINCT kg.coll_code(x.coll) || ':' || x.id)
        FROM oldtab o, LATERAL kg.edge_ends(o.coll, o.from_coll, o.from_id, o.to_coll, o.to_id, o.doc) x
        WHERE NOT EXISTS (SELECT 1 FROM newtab e WHERE e.org_id = o.org_id AND e.coll = o.coll AND e.from_coll = o.from_coll AND e.from_id = o.from_id
                            AND e.to_coll = o.to_coll AND e.to_id = o.to_id AND e.ekey = o.ekey) GROUP BY 1;
      INSERT INTO kg.graph_changes (org_id, ends)
        SELECT e.org_id, array_agg(DISTINCT kg.coll_code(x.coll) || ':' || x.id)
        FROM newtab e, LATERAL kg.edge_ends(e.coll, e.from_coll, e.from_id, e.to_coll, e.to_id, e.doc) x
        WHERE NOT EXISTS (SELECT 1 FROM oldtab o WHERE e.org_id = o.org_id AND e.coll = o.coll AND e.from_coll = o.from_coll AND e.from_id = o.from_id
                            AND e.to_coll = o.to_coll AND e.to_id = o.to_id AND e.ekey = o.ekey) GROUP BY 1;
    END IF;
  END IF;
  RETURN NULL;
END $fn$;

CREATE FUNCTION kg.refuse_truncate() RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $fn$
BEGIN
  RAISE EXCEPTION 'truncating graph.nodes or graph.edges would leave kg stale' USING ERRCODE = '0A000';
END $fn$;

SELECT kernel.exempt_trigger('kg.log_graph_change()', 'inserts the keys of the changed nodes into kg.graph_changes as the writer of graph.*, in its transaction; reads nothing but its transition tables', 'security-review');

CREATE TRIGGER kg_nodes_insert AFTER INSERT ON graph.nodes REFERENCING NEW TABLE AS newtab FOR EACH STATEMENT EXECUTE FUNCTION kg.log_graph_change();
CREATE TRIGGER kg_nodes_update AFTER UPDATE ON graph.nodes REFERENCING OLD TABLE AS oldtab NEW TABLE AS newtab FOR EACH STATEMENT EXECUTE FUNCTION kg.log_graph_change();
CREATE TRIGGER kg_nodes_delete AFTER DELETE ON graph.nodes REFERENCING OLD TABLE AS oldtab FOR EACH STATEMENT EXECUTE FUNCTION kg.log_graph_change();
CREATE TRIGGER kg_edges_insert AFTER INSERT ON graph.edges REFERENCING NEW TABLE AS newtab FOR EACH STATEMENT EXECUTE FUNCTION kg.log_graph_change();
CREATE TRIGGER kg_edges_update AFTER UPDATE ON graph.edges REFERENCING OLD TABLE AS oldtab NEW TABLE AS newtab FOR EACH STATEMENT EXECUTE FUNCTION kg.log_graph_change();
CREATE TRIGGER kg_edges_delete AFTER DELETE ON graph.edges REFERENCING OLD TABLE AS oldtab FOR EACH STATEMENT EXECUTE FUNCTION kg.log_graph_change();
CREATE TRIGGER kg_nodes_truncate BEFORE TRUNCATE ON graph.nodes FOR EACH STATEMENT EXECUTE FUNCTION kg.refuse_truncate();
CREATE TRIGGER kg_edges_truncate BEFORE TRUNCATE ON graph.edges FOR EACH STATEMENT EXECUTE FUNCTION kg.refuse_truncate();
-- the triggers fire under session_replication_role = replica too (a pg_restore --disable-triggers is refused by the kernel's restore path instead)
ALTER TABLE graph.nodes ENABLE ALWAYS TRIGGER kg_nodes_insert, ENABLE ALWAYS TRIGGER kg_nodes_update, ENABLE ALWAYS TRIGGER kg_nodes_delete, ENABLE ALWAYS TRIGGER kg_nodes_truncate;
ALTER TABLE graph.edges ENABLE ALWAYS TRIGGER kg_edges_insert, ENABLE ALWAYS TRIGGER kg_edges_update, ENABLE ALWAYS TRIGGER kg_edges_delete, ENABLE ALWAYS TRIGGER kg_edges_truncate;
