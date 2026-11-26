-- Session erasure jobs (`agentSessionErasures`): one per session however often it is asked for, claimed under a lease
-- on database time. The due queue is read across orgs by the jobs role, so its index leads with the due time.
CREATE TABLE harness.session_erasures (
  org_id text NOT NULL,
  session_id text COLLATE "C" NOT NULL,
  status text GENERATED ALWAYS AS (doc->>'status') STORED,
  next_attempt_at_ms bigint GENERATED ALWAYS AS ((doc->>'nextAttemptAt')::bigint) STORED,
  lease_expires_at_ms bigint GENERATED ALWAYS AS ((doc->>'leaseExpiresAt')::bigint) STORED,
  doc jsonb NOT NULL,
  region text NOT NULL,
  PRIMARY KEY (org_id, session_id)
);
CREATE INDEX session_erasures_due ON harness.session_erasures (next_attempt_at_ms) WHERE status = 'pending';
CREATE INDEX session_erasures_pending_by_org ON harness.session_erasures (org_id) WHERE status = 'pending';

-- Pending agent erasures (`agentErasures`): one per agent, from its delete until its memories are erased.
CREATE TABLE harness.agent_erasures (
  org_id text NOT NULL,
  agent_key text COLLATE "C" NOT NULL,
  next_attempt_at_ms bigint GENERATED ALWAYS AS ((doc->>'nextAttemptAt')::bigint) STORED,
  doc jsonb NOT NULL,
  region text NOT NULL,
  PRIMARY KEY (org_id, agent_key)
);
CREATE INDEX agent_erasures_due ON harness.agent_erasures (next_attempt_at_ms);

-- Org and user deletions (`agentPrincipalErasures`): `<orgId>:org` or `<orgId>:u:<userId>`, removed once done.
CREATE TABLE harness.principal_erasures (
  org_id text NOT NULL,
  id text COLLATE "C" NOT NULL,
  kind text GENERATED ALWAYS AS (doc->>'kind') STORED,
  step text GENERATED ALWAYS AS (doc->>'step') STORED,
  requested_at_ms bigint GENERATED ALWAYS AS ((doc->>'requestedAt')::bigint) STORED,
  next_attempt_at_ms bigint GENERATED ALWAYS AS ((doc->>'nextAttemptAt')::bigint) STORED,
  doc jsonb NOT NULL,
  region text NOT NULL,
  PRIMARY KEY (org_id, id)
);
CREATE INDEX principal_erasures_due ON harness.principal_erasures (next_attempt_at_ms);

-- The E7B backfill: a plan is the operator's and may cover every org (no org of its own); its items and the orgs'
-- completion markers are each org's. The plan's items are read across orgs by digest.
CREATE TABLE harness.erasure_backfill_plans (
  digest text COLLATE "C" PRIMARY KEY,
  state text GENERATED ALWAYS AS (doc->>'state') STORED,
  org_filter text GENERATED ALWAYS AS (doc->>'orgFilter') STORED,
  doc jsonb NOT NULL,
  region text NOT NULL
);
CREATE INDEX erasure_backfill_plans_by_org ON harness.erasure_backfill_plans (org_filter) WHERE org_filter IS NOT NULL;

CREATE TABLE harness.erasure_backfill_items (
  org_id text NOT NULL,
  digest text COLLATE "C" NOT NULL,
  session_id text COLLATE "C" NOT NULL,
  batch int GENERATED ALWAYS AS ((doc->>'batch')::int) STORED,
  doc jsonb NOT NULL,
  region text NOT NULL,
  PRIMARY KEY (org_id, digest, session_id)
);
CREATE INDEX erasure_backfill_items_by_session ON harness.erasure_backfill_items (digest, session_id);
CREATE INDEX erasure_backfill_items_by_batch ON harness.erasure_backfill_items (org_id, digest, batch, session_id);

CREATE TABLE harness.erasure_backfill_markers (
  org_id text NOT NULL PRIMARY KEY,
  doc jsonb NOT NULL,
  region text NOT NULL
);

SELECT kernel.apply_rls('harness.session_erasures', 'org', p_jobs => true);
SELECT kernel.apply_rls('harness.agent_erasures', 'org', p_jobs => true);
SELECT kernel.apply_rls('harness.principal_erasures', 'org', p_jobs => true);
SELECT kernel.apply_rls('harness.erasure_backfill_items', 'org', p_jobs => true);
SELECT kernel.apply_rls('harness.erasure_backfill_markers', 'org', p_jobs => true);
SELECT kernel.register_internal('harness.erasure_backfill_plans');
GRANT SELECT, INSERT, UPDATE, DELETE ON harness.session_erasures, harness.agent_erasures, harness.principal_erasures,
  harness.erasure_backfill_items, harness.erasure_backfill_markers TO pipeshub_node, pipeshub_jobs;
GRANT SELECT, INSERT, UPDATE, DELETE ON harness.erasure_backfill_plans TO pipeshub_node, pipeshub_jobs;
