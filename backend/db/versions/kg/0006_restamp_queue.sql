-- The restamp queue and the mark that hides a record until its restamp is done.
-- A change that outdates more stamps than fit in the writer's unit marks the records it reaches (kg.records.fence_job = the id of the job that will restamp them) and queues the
-- job (kg.restamp_queue) in that same unit; a reader never answers from a stamp that carries a mark. Both are written by the vetted writers only (kg_0006), read by the services.
-- The id of a restamp job, which is also the mark it leaves on the rows it hides.
CREATE SEQUENCE kg.job_id_seq AS bigint;

ALTER TABLE kg.records ADD COLUMN fence_job bigint;

CREATE TABLE kg.restamp_queue (
  org_id text NOT NULL, id bigint NOT NULL, app_sk bigint NOT NULL, kind text NOT NULL CHECK (kind IN ('rows', 'app')), reason text NOT NULL, fenced boolean NOT NULL,
  enqueued_ms bigint NOT NULL, claimed_by text, heartbeat_ms bigint NOT NULL DEFAULT 0, attempts int NOT NULL DEFAULT 0, cursor text, PRIMARY KEY (org_id, id)
);
ALTER TABLE kg.restamp_queue SET (autovacuum_analyze_scale_factor = 0.02, autovacuum_analyze_threshold = 200, autovacuum_vacuum_scale_factor = 0.05, autovacuum_vacuum_insert_scale_factor = 0.02);

SELECT kernel.apply_rls('kg.restamp_queue', 'org', p_vetted_write => true);
SELECT kernel.require_vetted_org_policy('kg.restamp_queue');
GRANT SELECT ON kg.restamp_queue TO pipeshub_connectors, pipeshub_indexing, pipeshub_query;
GRANT USAGE ON SEQUENCE kg.job_id_seq TO pipeshub_connectors;
