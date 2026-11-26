-- Execution: runs and their admission, as the Mongo stores keep them (`agentRuns`, `agentEvents`, `agentEventCounters`,
-- `agentCommandReceipts`, `agentAdmissionReceipts`, `agentAdmissionQuotas`, `agentNextRuns`) and two deployment-wide
-- plumbing tables (`agentHarnessLiveness`, `agentSandboxReaps`). A run's `doc` is the run document; every field a query
-- filters or sorts on is a generated column of it, so a store writes the document whole under its row lock and the
-- columns cannot drift from it. Times are epoch milliseconds on the database clock, as the documents hold them.
CREATE TABLE harness.runs (
  org_id text NOT NULL,
  run_id text COLLATE "C" NOT NULL,
  doc jsonb NOT NULL,
  region text NOT NULL,
  session_id text COLLATE "C" GENERATED ALWAYS AS (doc->>'sessionId') STORED,
  actor_id text COLLATE "C" GENERATED ALWAYS AS (doc->>'actorId') STORED,
  agent_key text COLLATE "C" GENERATED ALWAYS AS (doc->>'agentKey') STORED,
  status text COLLATE "C" GENERATED ALWAYS AS (doc->>'status') STORED,
  harness_major integer GENERATED ALWAYS AS (COALESCE((doc->>'harnessMajor')::integer, 1)) STORED,
  profile text COLLATE "C" GENERATED ALWAYS AS (doc->>'profile') STORED,
  revision bigint GENERATED ALWAYS AS ((doc->>'revision')::bigint) STORED,
  attempts integer GENERATED ALWAYS AS (COALESCE((doc->>'attempts')::integer, 0)) STORED,
  priority_rank integer GENERATED ALWAYS AS ((doc->>'priorityRank')::integer) STORED,
  not_before double precision GENERATED ALWAYS AS ((doc->>'notBefore')::double precision) STORED,
  lease_expires_at double precision GENERATED ALWAYS AS ((doc->>'leaseExpiresAt')::double precision) STORED,
  absolute_deadline_at double precision GENERATED ALWAYS AS ((doc->>'absoluteDeadlineAt')::double precision) STORED,
  created_at double precision GENERATED ALWAYS AS ((doc->>'createdAt')::double precision) STORED,
  updated_at double precision GENERATED ALWAYS AS ((doc->>'updatedAt')::double precision) STORED,
  parent_run_id text COLLATE "C" GENERATED ALWAYS AS (doc->'parent'->>'runId') STORED,
  triggered boolean GENERATED ALWAYS AS (COALESCE(jsonb_typeof(doc->'trigger'), 'null') <> 'null') STORED,
  waiting_since double precision GENERATED ALWAYS AS ((doc->'waitingForWorker'->>'since')::double precision) STORED,
  -- The run holds its session (`activeSessionKey` set from admission until it ends).
  active boolean GENERATED ALWAYS AS ((doc->>'activeSessionKey') IS NOT NULL) STORED,
  PRIMARY KEY (org_id, run_id)
);
-- One active root run per session: a second admission's insert fails here, as Mongo's unique `activeSessionKey`.
CREATE UNIQUE INDEX runs_one_active_per_session ON harness.runs (org_id, session_id) WHERE active;
-- The claim queue (D20, G-45): queued v2 runs by class, then oldest notBefore; the floor claim reads it without class.
CREATE INDEX runs_claim_by_priority ON harness.runs (priority_rank, not_before, created_at)
  WHERE status = 'queued' AND harness_major = 2;
CREATE INDEX runs_claim_floor ON harness.runs (not_before, created_at) WHERE status = 'queued' AND harness_major = 2;
CREATE INDEX runs_inline_queued ON harness.runs (not_before) WHERE status = 'queued' AND profile = 'inline';
CREATE INDEX runs_unclaimed ON harness.runs (updated_at)
  WHERE status = 'queued' AND attempts = 0 AND parent_run_id IS NULL AND NOT triggered AND waiting_since IS NULL;
-- The sweeper: worker-owned runs by lease, idle runs by deadline.
CREATE INDEX runs_by_lease ON harness.runs (lease_expires_at) WHERE status IN ('running', 'cancelling');
CREATE INDEX runs_by_deadline ON harness.runs (status, absolute_deadline_at);
-- The stranded-run scan (DUR2): queued runs a worker was refused for, longest wait first.
CREATE INDEX runs_stranded ON harness.runs (waiting_since) WHERE status = 'queued' AND waiting_since IS NOT NULL;
-- Admission quotas count an org's and an actor's active runs (P1.14); a deactivation lists an actor's (SEC-AZ-08).
CREATE INDEX runs_by_org_status_actor ON harness.runs (org_id, status, actor_id);
-- The audit search (J-18): a person's or an agent's runs, newest first.
CREATE INDEX runs_by_actor ON harness.runs (org_id, actor_id, created_at DESC);
CREATE INDEX runs_by_agent ON harness.runs (org_id, agent_key, created_at DESC);
-- A session's runs (erasure, retention, history labels, the run tree): runs_by_session, built by harness_0009 with the
-- insertion order that breaks a created_at tie.
-- A parent's open children (P6.1), and children left by an ended parent, oldest first.
CREATE INDEX runs_by_parent ON harness.runs (org_id, parent_run_id, status) WHERE parent_run_id IS NOT NULL;
CREATE INDEX runs_children_by_created ON harness.runs (created_at) WHERE parent_run_id IS NOT NULL;

-- The v1 per-session event log (`agentEvents`): a gap-free eventSeq per session from its counter row.
CREATE TABLE harness.run_events (
  org_id text NOT NULL,
  session_id text COLLATE "C" NOT NULL,
  event_seq bigint NOT NULL CHECK (event_seq > 0),
  run_id text COLLATE "C" NOT NULL,
  commit_seq bigint NOT NULL,
  doc jsonb NOT NULL,
  region text NOT NULL,
  PRIMARY KEY (org_id, session_id, event_seq)
);
CREATE INDEX run_events_by_run ON harness.run_events (org_id, run_id, commit_seq);

-- Per session (`agentEventCounters`): the v1 event counter, the features its root stream needs folded (SS1), everyone a
-- run was admitted for (SS2), and the person's waiting /compact (SLASH2 C2). The row lock orders the session's writers.
CREATE TABLE harness.session_counters (
  org_id text NOT NULL,
  session_id text COLLATE "C" NOT NULL,
  next_event_seq bigint NOT NULL DEFAULT 0 CHECK (next_event_seq >= 0),
  journal_features text[] NOT NULL DEFAULT '{}',
  actors text[] NOT NULL DEFAULT '{}',
  compact_request jsonb,
  region text NOT NULL,
  PRIMARY KEY (org_id, session_id)
);

-- A worker command's receipt (`agentCommandReceipts`): its retry replays it; another payload under its id is refused.
CREATE TABLE harness.command_receipts (
  org_id text NOT NULL,
  run_id text COLLATE "C" NOT NULL,
  command_id text COLLATE "C" NOT NULL,
  payload_hash text NOT NULL,
  receipt jsonb NOT NULL,
  created_at double precision NOT NULL,
  region text NOT NULL,
  PRIMARY KEY (org_id, run_id, command_id)
);
-- The receipt TTL pass reads the oldest first (T5).
CREATE INDEX command_receipts_by_age ON harness.command_receipts (created_at);

-- An admission's receipt (`agentAdmissionReceipts`), keyed by its idempotency scope; it holds the admitted run and its
-- sealed input, so it is erased with the session.
CREATE TABLE harness.admission_receipts (
  org_id text NOT NULL,
  scope_key text COLLATE "C" NOT NULL,
  payload_hash text NOT NULL,
  result jsonb NOT NULL,
  session_id text COLLATE "C" GENERATED ALWAYS AS (result->>'sessionId') STORED,
  created_at double precision NOT NULL,
  region text NOT NULL,
  PRIMARY KEY (org_id, scope_key)
);
CREATE INDEX admission_receipts_by_age ON harness.admission_receipts (created_at);
CREATE INDEX admission_receipts_by_session ON harness.admission_receipts (org_id, session_id);

-- One fixed admission-rate window per org and per actor (`agentAdmissionQuotas`), with the org's admissions in flight.
CREATE TABLE harness.admission_quotas (
  org_id text NOT NULL,
  key text COLLATE "C" NOT NULL,
  window_start double precision NOT NULL,
  count integer NOT NULL CHECK (count >= 0),
  holds jsonb NOT NULL DEFAULT '[]',
  region text NOT NULL,
  PRIMARY KEY (org_id, key)
);

-- Messages waiting for someone else's running task to end (`agentNextRuns`, SS2).
CREATE TABLE harness.next_runs (
  org_id text NOT NULL,
  id text COLLATE "C" NOT NULL,
  doc jsonb NOT NULL,
  region text NOT NULL,
  session_id text COLLATE "C" GENERATED ALWAYS AS (doc->>'sessionId') STORED,
  status text COLLATE "C" GENERATED ALWAYS AS (doc->>'status') STORED,
  created_at double precision GENERATED ALWAYS AS ((doc->>'createdAt')::double precision) STORED,
  PRIMARY KEY (org_id, id)
);
CREATE INDEX next_runs_by_session ON harness.next_runs (org_id, session_id, status, created_at);
CREATE INDEX next_runs_pending ON harness.next_runs (created_at) WHERE status = 'pending';

-- Deployment-wide liveness records (`agentHarnessLiveness`: the control plane's, and one per worker feature).
-- seen_at is NULL for a record only a holdoff wrote (ordered before the first sighting).
CREATE TABLE harness.liveness (
  id text COLLATE "C" PRIMARY KEY,
  seen_at double precision,
  previous_seen_at double precision,
  holdoff_until double precision NOT NULL DEFAULT 0
);

-- Sandbox reaps the broker refused (`agentSandboxReaps`, CLEANUP-G6): a container's label set, ids only, asked again until
-- the broker answers. Deployment plumbing: a sandbox's labels (kept whole) may name no org.
CREATE TABLE harness.sandbox_reaps (
  id text COLLATE "C" PRIMARY KEY,
  labels jsonb NOT NULL,
  failures integer NOT NULL,
  next_attempt_at double precision NOT NULL,
  created_at double precision NOT NULL,
  last_error_type text
);
CREATE INDEX sandbox_reaps_due ON harness.sandbox_reaps (next_attempt_at);

SELECT kernel.apply_rls('harness.runs', 'org', p_jobs => true);
SELECT kernel.apply_rls('harness.run_events', 'org', p_jobs => true);
SELECT kernel.apply_rls('harness.session_counters', 'org', p_jobs => true);
SELECT kernel.apply_rls('harness.command_receipts', 'org', p_jobs => true);
SELECT kernel.apply_rls('harness.admission_receipts', 'org', p_jobs => true);
SELECT kernel.apply_rls('harness.admission_quotas', 'org', p_jobs => true);
SELECT kernel.apply_rls('harness.next_runs', 'org', p_jobs => true);
SELECT kernel.register_internal('harness.liveness');
SELECT kernel.register_internal('harness.sandbox_reaps');
GRANT SELECT, INSERT, UPDATE, DELETE ON harness.runs, harness.run_events, harness.session_counters,
  harness.command_receipts, harness.admission_receipts, harness.admission_quotas, harness.next_runs
  TO pipeshub_node, pipeshub_jobs;
GRANT SELECT, INSERT, UPDATE, DELETE ON harness.liveness, harness.sandbox_reaps TO pipeshub_node;
