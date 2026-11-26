-- The record counts of a connector or collection (get_connector_stats: by record type and by indexing, extraction and entities state), kept so that a read costs one
-- index range per connector instead of a walk of every record it holds.
-- graph.stats_records holds, per counted record and per app that counts it, the key it is counted under; graph.stats_counts holds the rows of each (app, key), summed.
-- Both are written by the projection of app/services/graph_db/postgres/connector_stats.py alone, one organisation at a time under its lock; graph.stats_apps marks the apps
-- it has counted whole. A write to graph.nodes or graph.edges that can change a count logs, in its own transaction, the keys it touched (graph.stats_changes): 'r:' a record,
-- 's:' an entities stage state, 'g:' a record group whose belongs-to link changed, 'a:' an app. The projection recounts what they name and deletes them. A reader adds to the
-- counts the change of the records still logged, in the same snapshot, so an answer is the walk's at that snapshot; the walk is read instead until an app is counted whole.
-- Missing values are kept as '' (the answer treats a missing and an empty status or type alike: neither is a status, neither names a type).
CREATE TABLE graph.stats_changes (org_id text NOT NULL, id bigint GENERATED ALWAYS AS IDENTITY, keys text[] NOT NULL CHECK (cardinality(keys) > 0), PRIMARY KEY (org_id, id));
CREATE TABLE graph.stats_records (
  org_id text NOT NULL, record_id text NOT NULL, app_id text NOT NULL, record_type text NOT NULL, indexing text NOT NULL, extraction text NOT NULL, entities text NOT NULL,
  sid text, PRIMARY KEY (org_id, record_id, app_id)
);
CREATE INDEX stats_records_app ON graph.stats_records (org_id, app_id);
CREATE INDEX stats_records_sid ON graph.stats_records (org_id, sid) WHERE sid IS NOT NULL;      -- the records a changed stage state counts under
CREATE TABLE graph.stats_counts (
  org_id text NOT NULL, app_id text NOT NULL, record_type text NOT NULL, indexing text NOT NULL, extraction text NOT NULL, entities text NOT NULL, n bigint NOT NULL,
  PRIMARY KEY (org_id, app_id, record_type, indexing, extraction, entities)
);
CREATE TABLE graph.stats_apps (org_id text NOT NULL, app_id text NOT NULL, PRIMARY KEY (org_id, app_id));
ALTER TABLE graph.stats_changes SET (autovacuum_analyze_scale_factor = 0.02, autovacuum_analyze_threshold = 200, autovacuum_vacuum_scale_factor = 0.05);
ALTER TABLE graph.stats_records SET (autovacuum_analyze_scale_factor = 0.02, autovacuum_analyze_threshold = 200, autovacuum_vacuum_scale_factor = 0.05);
ALTER TABLE graph.stats_counts SET (autovacuum_analyze_scale_factor = 0.02, autovacuum_analyze_threshold = 200, autovacuum_vacuum_scale_factor = 0.05);
ALTER TABLE graph.stats_apps SET (autovacuum_analyze_scale_factor = 0.02, autovacuum_analyze_threshold = 200, autovacuum_vacuum_scale_factor = 0.05);

SELECT kernel.apply_rls('graph.stats_changes', 'org');
SELECT kernel.apply_rls('graph.stats_records', 'org');
SELECT kernel.apply_rls('graph.stats_counts', 'org');
SELECT kernel.apply_rls('graph.stats_apps', 'org');
-- the connectors service projects; every service that writes the graph logs (the trigger runs as the writer) and may read the counts
GRANT SELECT, INSERT, UPDATE, DELETE ON graph.stats_changes, graph.stats_records, graph.stats_counts, graph.stats_apps TO pipeshub_connectors;
GRANT SELECT, INSERT ON graph.stats_changes TO pipeshub_indexing, pipeshub_query;
GRANT SELECT ON graph.stats_records, graph.stats_counts, graph.stats_apps TO pipeshub_indexing, pipeshub_query;

-- One row per statement and org, as kg.log_graph_change: a writer pays one insert per statement, not one per row. An upsert that changes nothing a count reads logs
-- nothing. A restore adds nothing: the apps it restores are counted whole again (graph_stats_apps_restore below). enable_nestloop and jit are off for kg_0004's reason.
-- The tests are written out, not in helper functions: a SQL function called per row of a transition table cost ten times the update it logs (500 records: 600 ms
-- against 65 ms). What a count reads of a node: a record's type, statuses, flags, mime type and revision; an entities stage state's status; an app's type. A
-- belongs-to link counts from its source: a record's own membership ('r:'), or every record under a record group ('g:').
CREATE FUNCTION graph.stats_log_change() RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, graph, pg_temp SET enable_nestloop = off SET jit = off AS $fn$
BEGIN
  IF current_user = 'pipeshub_restore' THEN
    RETURN NULL;
  END IF;
  IF TG_TABLE_NAME = 'nodes' THEN
    IF TG_OP = 'INSERT' THEN
      INSERT INTO graph.stats_changes (org_id, keys)
        SELECT n.org_id, array_agg(DISTINCT CASE n.coll WHEN 'records' THEN 'r:' WHEN 'apps' THEN 'a:' ELSE 's:' END || n.id) FROM newtab n
        WHERE n.coll IN ('records', 'apps') OR (n.coll = 'stageStates' AND right(n.id, 9) = ':entities') GROUP BY 1;
    ELSIF TG_OP = 'DELETE' THEN
      INSERT INTO graph.stats_changes (org_id, keys)
        SELECT o.org_id, array_agg(DISTINCT CASE o.coll WHEN 'records' THEN 'r:' WHEN 'apps' THEN 'a:' ELSE 's:' END || o.id) FROM oldtab o
        WHERE o.coll IN ('records', 'apps') OR (o.coll = 'stageStates' AND right(o.id, 9) = ':entities') GROUP BY 1;
    ELSE
      INSERT INTO graph.stats_changes (org_id, keys) SELECT c.org_id, array_agg(DISTINCT CASE c.coll WHEN 'records' THEN 'r:' WHEN 'apps' THEN 'a:' ELSE 's:' END || c.id) FROM (
          SELECT n.org_id, n.coll, n.id FROM newtab n LEFT JOIN oldtab o ON o.org_id = n.org_id AND o.coll = n.coll AND o.id = n.id
          WHERE (n.coll = 'records' AND (o.id IS NULL OR (n.doc->'recordType', n.doc->'indexingStatus', n.doc->'extractionStatus', n.doc->'isInternal', n.doc->'isPlaceholder',
                   n.doc->'isDeleted', n.doc->'mimeType', n.doc->'virtualRecordId', n.doc->'contentRev') IS DISTINCT FROM (o.doc->'recordType', o.doc->'indexingStatus',
                   o.doc->'extractionStatus', o.doc->'isInternal', o.doc->'isPlaceholder', o.doc->'isDeleted', o.doc->'mimeType', o.doc->'virtualRecordId', o.doc->'contentRev')))
             OR (n.coll = 'stageStates' AND right(n.id, 9) = ':entities' AND (o.id IS NULL OR n.doc->'status' IS DISTINCT FROM o.doc->'status'))
             OR (n.coll = 'apps' AND (o.id IS NULL OR n.doc->'type' IS DISTINCT FROM o.doc->'type'))
          UNION ALL SELECT o.org_id, o.coll, o.id FROM oldtab o LEFT JOIN newtab n ON n.org_id = o.org_id AND n.coll = o.coll AND n.id = o.id
          WHERE n.id IS NULL AND (o.coll IN ('records', 'apps') OR (o.coll = 'stageStates' AND right(o.id, 9) = ':entities'))) c
        GROUP BY 1;
    END IF;
  ELSIF TG_OP = 'INSERT' THEN
    INSERT INTO graph.stats_changes (org_id, keys)
      SELECT e.org_id, array_agg(DISTINCT CASE e.from_coll WHEN 'records' THEN 'r:' ELSE 'g:' END || e.from_id) FROM newtab e
      WHERE e.coll = 'belongsTo' AND e.from_coll IN ('records', 'recordGroups') AND e.to_coll IN ('recordGroups', 'apps') GROUP BY 1;
  ELSIF TG_OP = 'DELETE' THEN
    INSERT INTO graph.stats_changes (org_id, keys)
      SELECT e.org_id, array_agg(DISTINCT CASE e.from_coll WHEN 'records' THEN 'r:' ELSE 'g:' END || e.from_id) FROM oldtab e
      WHERE e.coll = 'belongsTo' AND e.from_coll IN ('records', 'recordGroups') AND e.to_coll IN ('recordGroups', 'apps') GROUP BY 1;
  ELSE      -- a link's document does not count: only a link whose ends changed (an update of the key columns) is logged, both its old and its new source
    INSERT INTO graph.stats_changes (org_id, keys) SELECT c.org_id, array_agg(DISTINCT CASE c.from_coll WHEN 'records' THEN 'r:' ELSE 'g:' END || c.from_id) FROM (
        SELECT n.org_id, n.from_coll, n.from_id, n.coll, n.to_coll FROM newtab n WHERE NOT EXISTS (SELECT 1 FROM oldtab o
          WHERE o.org_id = n.org_id AND o.coll = n.coll AND o.from_coll = n.from_coll AND o.from_id = n.from_id AND o.to_coll = n.to_coll AND o.to_id = n.to_id AND o.ekey = n.ekey)
        UNION ALL SELECT o.org_id, o.from_coll, o.from_id, o.coll, o.to_coll FROM oldtab o WHERE NOT EXISTS (SELECT 1 FROM newtab n
          WHERE o.org_id = n.org_id AND o.coll = n.coll AND o.from_coll = n.from_coll AND o.from_id = n.from_id AND o.to_coll = n.to_coll AND o.to_id = n.to_id AND o.ekey = n.ekey)) c
      WHERE c.coll = 'belongsTo' AND c.from_coll IN ('records', 'recordGroups') AND c.to_coll IN ('recordGroups', 'apps') GROUP BY 1;
  END IF;
  RETURN NULL;
END $fn$;
SELECT kernel.exempt_trigger('graph.stats_log_change()', 'inserts the keys of the changed nodes and belongs-to links into graph.stats_changes as the writer of graph.*, in its transaction; reads nothing but its transition tables', 'security-review');

CREATE TRIGGER stats_nodes_insert AFTER INSERT ON graph.nodes REFERENCING NEW TABLE AS newtab FOR EACH STATEMENT EXECUTE FUNCTION graph.stats_log_change();
CREATE TRIGGER stats_nodes_update AFTER UPDATE ON graph.nodes REFERENCING OLD TABLE AS oldtab NEW TABLE AS newtab FOR EACH STATEMENT EXECUTE FUNCTION graph.stats_log_change();
CREATE TRIGGER stats_nodes_delete AFTER DELETE ON graph.nodes REFERENCING OLD TABLE AS oldtab FOR EACH STATEMENT EXECUTE FUNCTION graph.stats_log_change();
CREATE TRIGGER stats_edges_insert AFTER INSERT ON graph.edges REFERENCING NEW TABLE AS newtab FOR EACH STATEMENT EXECUTE FUNCTION graph.stats_log_change();
CREATE TRIGGER stats_edges_update AFTER UPDATE ON graph.edges REFERENCING OLD TABLE AS oldtab NEW TABLE AS newtab FOR EACH STATEMENT EXECUTE FUNCTION graph.stats_log_change();
CREATE TRIGGER stats_edges_delete AFTER DELETE ON graph.edges REFERENCING OLD TABLE AS oldtab FOR EACH STATEMENT EXECUTE FUNCTION graph.stats_log_change();
ALTER TABLE graph.nodes ENABLE ALWAYS TRIGGER stats_nodes_insert, ENABLE ALWAYS TRIGGER stats_nodes_update, ENABLE ALWAYS TRIGGER stats_nodes_delete;
ALTER TABLE graph.edges ENABLE ALWAYS TRIGGER stats_edges_insert, ENABLE ALWAYS TRIGGER stats_edges_update, ENABLE ALWAYS TRIGGER stats_edges_delete;

-- A restored mark is not kept: the restore wrote graph.* without a log, so each app is counted whole again from the restored rows (as kg_0015 does for kg.counts).
CREATE FUNCTION graph.stats_app_restored() RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $fn$
BEGIN
  RETURN NULL;
END $fn$;
SELECT kernel.exempt_trigger('graph.stats_app_restored()', 'drops a counted-whole mark the restore role inserts into graph.stats_apps; reads and writes nothing', 'security-review');
CREATE TRIGGER graph_stats_apps_restore BEFORE INSERT ON graph.stats_apps FOR EACH ROW WHEN (current_user = 'pipeshub_restore') EXECUTE FUNCTION graph.stats_app_restored();
-- A trigger runs its function whatever the writer's privileges; no role calls them, and only the graph's writers hold anything in schema graph.
REVOKE EXECUTE ON FUNCTION graph.stats_log_change(), graph.stats_app_restored() FROM PUBLIC;
