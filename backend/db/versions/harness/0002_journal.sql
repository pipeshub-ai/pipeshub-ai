-- The typed session journal (`sessionEvents`): one row per event, a gap-free seq per stream allocated by the row lock
-- on the stream's counter, held to commit, so seq order is commit order and a reader never sees a hole fill in later.
CREATE TABLE harness.session_events (
  org_id text NOT NULL,
  stream_id text COLLATE "C" NOT NULL,
  seq bigint NOT NULL CHECK (seq > 0),
  session_id text COLLATE "C" NOT NULL,
  run_id text COLLATE "C",
  ts timestamptz NOT NULL,
  doc jsonb NOT NULL,
  region text NOT NULL,
  PRIMARY KEY (org_id, stream_id, seq)
);
CREATE INDEX session_events_by_run ON harness.session_events (org_id, run_id, seq) WHERE run_id IS NOT NULL;
CREATE INDEX session_events_by_session ON harness.session_events (org_id, session_id, stream_id, seq);

CREATE TABLE harness.stream_counters (
  org_id text NOT NULL,
  stream_id text COLLATE "C" NOT NULL,
  last_seq bigint NOT NULL CHECK (last_seq >= 0),
  ts timestamptz NOT NULL,
  region text NOT NULL,
  PRIMARY KEY (org_id, stream_id)
);

-- Claim-checked event payloads (`agentJournalPayloads`): sealed bytes, keyed by their own id.
CREATE TABLE harness.journal_payloads (
  org_id text NOT NULL,
  id text COLLATE "C" NOT NULL,
  hash text NOT NULL,
  size bigint NOT NULL CHECK (size >= 0),
  bytes bytea NOT NULL,
  created_at timestamptz NOT NULL,
  owner_session_id text COLLATE "C",
  owner_run_id text COLLATE "C",
  referenced boolean,
  region text NOT NULL,
  PRIMARY KEY (org_id, id)
);
CREATE INDEX journal_payloads_by_owner ON harness.journal_payloads (org_id, owner_session_id) WHERE owner_session_id IS NOT NULL;
CREATE INDEX journal_payloads_unreferenced ON harness.journal_payloads (org_id, created_at) WHERE referenced = false;

-- Deployment-wide leader leases (`agentHarnessLeases`: the sweeper, the schedule firer): plumbing, no org data.
CREATE TABLE harness.leases (
  name text COLLATE "C" PRIMARY KEY,
  holder text NOT NULL,
  expires_at_ms bigint NOT NULL
);

SELECT kernel.apply_rls('harness.session_events', 'org', p_jobs => true);
SELECT kernel.apply_rls('harness.stream_counters', 'org', p_jobs => true);
SELECT kernel.apply_rls('harness.journal_payloads', 'org', p_jobs => true);
SELECT kernel.register_internal('harness.leases');
GRANT USAGE ON SCHEMA harness TO pipeshub_node, pipeshub_jobs;
GRANT SELECT, INSERT, UPDATE, DELETE ON harness.session_events, harness.stream_counters, harness.journal_payloads
  TO pipeshub_node, pipeshub_jobs;
GRANT SELECT, INSERT, UPDATE, DELETE ON harness.leases TO pipeshub_node;
