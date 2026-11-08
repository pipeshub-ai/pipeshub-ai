-- The Node job scheduler without Redis (the crawl and sync schedules): jobs with a state and a run time, and the repeat entries that
-- produce them. Claimed with FOR UPDATE SKIP LOCKED, so two Node processes never start the same run. Internal tables like the queue
-- tables of platform_0003: deployment-wide, no org column (the org is in the job data).
CREATE SCHEMA IF NOT EXISTS platform;

CREATE TABLE platform.sched_jobs (
  queue         text NOT NULL,
  id            text NOT NULL,
  name          text NOT NULL,
  data          jsonb NOT NULL,
  opts          jsonb NOT NULL DEFAULT '{}',
  state         text NOT NULL,                 -- waiting | delayed | active | completed | failed
  run_at        timestamptz NOT NULL,          -- not before; a delayed job's start, a waiting job's creation
  priority      int NOT NULL DEFAULT 0,        -- lower runs first
  created_ms    bigint NOT NULL,
  delay_ms      bigint,
  processed_ms  bigint,
  finished_ms   bigint,
  attempts_made int NOT NULL DEFAULT 0,
  progress      jsonb,
  failed_reason text,
  stalled       int NOT NULL DEFAULT 0,
  locked_by     text,
  locked_until  timestamptz,
  PRIMARY KEY (queue, id));
-- the next runnable job, and the next claim to expire
CREATE INDEX sched_jobs_due ON platform.sched_jobs (queue, priority, run_at) WHERE state IN ('waiting', 'delayed');
CREATE INDEX sched_jobs_active ON platform.sched_jobs (queue, locked_until) WHERE state = 'active';
CREATE INDEX sched_jobs_by_state ON platform.sched_jobs (queue, state, created_ms);

-- A schedule: the job it produces next is a row of sched_jobs; claiming that job inserts the one after it.
CREATE TABLE platform.sched_repeats (
  queue       text NOT NULL,
  key         text NOT NULL,
  name        text NOT NULL,
  job_id      text,
  pattern     text,
  every_ms    bigint,
  tz          text,
  end_date_ms bigint,
  next_ms     bigint,
  PRIMARY KEY (queue, key));

SELECT kernel.register_internal('platform.sched_jobs');
SELECT kernel.register_internal('platform.sched_repeats');
-- only the Node API schedules and runs jobs
GRANT SELECT, INSERT, UPDATE, DELETE ON platform.sched_jobs, platform.sched_repeats TO pipeshub_node;
