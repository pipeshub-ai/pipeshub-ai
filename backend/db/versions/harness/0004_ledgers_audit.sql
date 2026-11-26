-- The budget ledger (`agentBudgetAccounts`, `agentBudgetReservations`): one account per run of a tree, and the
-- reservations held on a run's path. Every operation of a tree takes its root account's row lock first, so the
-- tree's operations run one at a time and a cap holds under concurrent reservers.
CREATE TABLE harness.budget_accounts (
  org_id text NOT NULL,
  run_id text COLLATE "C" NOT NULL,
  root_run_id text COLLATE "C" NOT NULL,
  doc jsonb NOT NULL,
  region text NOT NULL,
  PRIMARY KEY (org_id, run_id)
);

CREATE TABLE harness.budget_reservations (
  org_id text NOT NULL,
  run_id text COLLATE "C" NOT NULL,
  key text COLLATE "C" NOT NULL,
  root_run_id text COLLATE "C" NOT NULL,
  state text NOT NULL CHECK (state IN ('open', 'committed', 'released', 'expired')),
  hold boolean NOT NULL,
  expires_at_ms bigint NOT NULL,
  doc jsonb NOT NULL,
  region text NOT NULL,
  PRIMARY KEY (org_id, run_id, key)
);
CREATE INDEX budget_reservations_open_by_root ON harness.budget_reservations (org_id, root_run_id, expires_at_ms)
  WHERE state = 'open';
CREATE INDEX budget_reservations_open_by_run ON harness.budget_reservations (org_id, run_id) WHERE state = 'open';
CREATE INDEX budget_reservations_open_holds ON harness.budget_reservations (org_id, expires_at_ms)
  WHERE hold AND state = 'open';

-- The audit chain (`agentAuditRecords`, `agentAuditHeads`, `agentAuditPending`): an append stores its entries in
-- the journal's unit; a link of its own puts them on the org's chain under the head's row lock, so the chain stays
-- linear whoever links.
CREATE TABLE harness.audit_records (
  org_id text NOT NULL,
  seq bigint NOT NULL CHECK (seq > 0),
  kind text COLLATE "C" NOT NULL,
  session_id text COLLATE "C",
  run_id text COLLATE "C",
  ts text COLLATE "C" NOT NULL,
  doc jsonb NOT NULL,
  region text NOT NULL,
  PRIMARY KEY (org_id, seq)
);
CREATE INDEX audit_records_by_kind ON harness.audit_records (org_id, kind, seq DESC);
CREATE INDEX audit_records_by_run ON harness.audit_records (org_id, run_id, seq DESC) WHERE run_id IS NOT NULL;

CREATE TABLE harness.audit_heads (
  org_id text NOT NULL,
  seq bigint NOT NULL CHECK (seq >= 0),
  hash text NOT NULL,
  anchored_seq bigint NOT NULL CHECK (anchored_seq >= 0),
  region text NOT NULL,
  PRIMARY KEY (org_id)
);
CREATE INDEX audit_heads_unanchored ON harness.audit_heads (org_id, anchored_seq) WHERE seq > anchored_seq;

CREATE TABLE harness.audit_pending (
  org_id text NOT NULL,
  id text COLLATE "C" NOT NULL,
  stream_id text COLLATE "C" NOT NULL,
  journal_seq bigint NOT NULL,
  ts text COLLATE "C" NOT NULL,
  doc jsonb NOT NULL,
  region text NOT NULL,
  PRIMARY KEY (org_id, id)
);
CREATE INDEX audit_pending_link_order ON harness.audit_pending (org_id, stream_id, journal_seq);

-- The local anchor sink (`agentAuditAnchors`): insert-only.
CREATE TABLE harness.audit_anchors (
  org_id text NOT NULL,
  seq bigint NOT NULL,
  hash text NOT NULL,
  anchored_at text NOT NULL,
  region text NOT NULL,
  PRIMARY KEY (org_id, seq)
);

-- The org's latest audit chain check (`agentAuditChainChecks`): one row per org, leased to one checker.
CREATE TABLE harness.audit_chain_checks (
  org_id text NOT NULL,
  check_id text COLLATE "C" GENERATED ALWAYS AS (doc ->> 'checkId') STORED,
  state text GENERATED ALWAYS AS (doc ->> 'state') STORED,
  holder text GENERATED ALWAYS AS (doc ->> 'holder') STORED,
  lease_until_ms bigint GENERATED ALWAYS AS ((doc ->> 'leaseUntil')::bigint) STORED,
  doc jsonb NOT NULL,
  region text NOT NULL,
  PRIMARY KEY (org_id)
);

-- Wrapped per-org data keys (`agentHarnessDataKeys`), every version until shredded.
CREATE TABLE harness.data_keys (
  org_id text NOT NULL,
  kid text COLLATE "C" NOT NULL,
  version integer NOT NULL CHECK (version > 0),
  kek_id text NOT NULL,
  wrapped text NOT NULL,
  created_at_ms bigint NOT NULL,
  seals bigint NOT NULL DEFAULT 0 CHECK (seals >= 0),
  region text NOT NULL,
  PRIMARY KEY (org_id, kid),
  UNIQUE (org_id, version)
);

-- The index of sandbox snapshots (`agentSandboxSnapshots`) the retention sweep reads.
CREATE TABLE harness.sandbox_snapshots (
  org_id text NOT NULL,
  object_id text COLLATE "C" NOT NULL,
  session_id text COLLATE "C" NOT NULL,
  run_id text COLLATE "C",
  taken_at timestamptz NOT NULL,
  snapshot_region text NOT NULL,
  region text NOT NULL,
  PRIMARY KEY (org_id, object_id)
);
CREATE INDEX sandbox_snapshots_by_taken ON harness.sandbox_snapshots (org_id, taken_at);
CREATE INDEX sandbox_snapshots_by_session ON harness.sandbox_snapshots (org_id, session_id);

SELECT kernel.apply_rls('harness.budget_accounts', 'org', p_jobs => true);
SELECT kernel.apply_rls('harness.budget_reservations', 'org', p_jobs => true);
SELECT kernel.apply_rls('harness.audit_records', 'org', p_jobs => true);
SELECT kernel.apply_rls('harness.audit_heads', 'org', p_jobs => true);
SELECT kernel.apply_rls('harness.audit_pending', 'org', p_jobs => true);
SELECT kernel.apply_rls('harness.audit_anchors', 'org', p_jobs => true);
SELECT kernel.apply_rls('harness.audit_chain_checks', 'org', p_jobs => true);
SELECT kernel.apply_rls('harness.data_keys', 'org', p_jobs => true);
SELECT kernel.apply_rls('harness.sandbox_snapshots', 'org', p_jobs => true);
GRANT SELECT, INSERT, UPDATE, DELETE ON harness.budget_accounts, harness.budget_reservations, harness.audit_records,
  harness.audit_heads, harness.audit_pending, harness.audit_anchors,
  harness.audit_chain_checks, harness.data_keys, harness.sandbox_snapshots
  TO pipeshub_node, pipeshub_jobs;
