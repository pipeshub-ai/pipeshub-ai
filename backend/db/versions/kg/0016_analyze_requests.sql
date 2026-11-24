-- The requests to refresh the planner statistics of the kg tables the knowledge-hub browse reads. A connector's first load into kg (its promotion to enforce) and its count
-- after a restore record one (kg.w_analyze_request, one row per org, a later ask moving its time forward); the maintenance worker (python -m db.maintain analyze, and every pass of
-- placement --watch) runs ANALYZE as the owner role and deletes the requests the new statistics cover. No application role may run ANALYZE (it needs MAINTAIN or ownership),
-- and until the statistics know an org the planner puts it at one row, so a browse may read every kg row of the org. The jobs role reads the requests and deletes them: the
-- delete is the one write left open to it (the writer's pin holds insert and update).
CREATE TABLE kg.analyze_requests (
  org_id text NOT NULL PRIMARY KEY,
  app_sk bigint NOT NULL,                              -- the connector whose load asked first (for the worker's log)
  requested_at timestamptz NOT NULL DEFAULT clock_timestamp()     -- when it was asked, not when the asking transaction began (a seed counts first)
);

SELECT kernel.apply_rls('kg.analyze_requests', 'org', p_jobs => true, p_vetted_write => true);
SELECT kernel.require_vetted_org_policy('kg.analyze_requests', '{delete}');
GRANT USAGE ON SCHEMA kg TO pipeshub_jobs;
GRANT SELECT, DELETE ON kg.analyze_requests TO pipeshub_jobs;
