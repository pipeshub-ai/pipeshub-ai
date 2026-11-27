-- Where the first count of an organisation's connector stats has got to (app/services/graph_db/postgres/connector_stats.py): the count recounts the organisation's records
-- in pages of record ids, one unit per page, so no statement or unit grows with a connector; ``after`` is the last record id counted (the next page starts after it), and
-- ``done`` says every record has been counted since the row was made. Until then the change log is recounted, never dropped, so a record written behind the page is
-- counted again. Written by the projection alone, under the organisation's stats lock.
CREATE TABLE graph.stats_sweeps (org_id text NOT NULL PRIMARY KEY, after text NOT NULL, done boolean NOT NULL DEFAULT false);
SELECT kernel.apply_rls('graph.stats_sweeps', 'org');
GRANT SELECT, INSERT, UPDATE, DELETE ON graph.stats_sweeps TO pipeshub_connectors;
-- As graph.stats_apps: a restored row is not kept, so a restored organisation is counted again from the restored rows.
CREATE TRIGGER graph_stats_sweeps_restore BEFORE INSERT ON graph.stats_sweeps FOR EACH ROW WHEN (current_user = 'pipeshub_restore')
  EXECUTE FUNCTION graph.stats_app_restored();
