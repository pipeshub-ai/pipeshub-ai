-- SIGNIN: a person's engine sign-in (`u:<userId>`) and the org's switch (`org`), apart from the user row.
CREATE TABLE harness.engine_signins (
  org_id text NOT NULL,
  subject text COLLATE "C" NOT NULL,
  user_id text COLLATE "C",
  doc jsonb NOT NULL,
  enabled boolean GENERATED ALWAYS AS ((doc->>'enabled')::boolean) STORED,
  signed_in_at bigint GENERATED ALWAYS AS ((doc->>'signedInAt')::bigint) STORED,
  region text NOT NULL,
  PRIMARY KEY (org_id, subject)
);

-- ENGPICK: the choices of the org (`org`), a person (`user:<id>`: their coding engine and memory switch) and a conversation
-- (`session:<id>`). They are the harness's own fields; the user, org and conversation rows stay as main's repositories keep them.
CREATE TABLE harness.personal_choices (
  org_id text NOT NULL,
  subject text COLLATE "C" NOT NULL,
  doc jsonb NOT NULL,
  region text NOT NULL,
  PRIMARY KEY (org_id, subject)
);

-- Workspace heads (`agentWorkspaceHeads`): the head moves only by compare-and-set on `revision`.
CREATE TABLE harness.workspace_heads (
  org_id text NOT NULL,
  workspace_id text COLLATE "C" NOT NULL,
  doc jsonb NOT NULL,
  revision bigint GENERATED ALWAYS AS ((doc->>'revision')::bigint) STORED,
  is_fork boolean GENERATED ALWAYS AS (jsonb_typeof(doc->'forkOf') = 'object') STORED,
  region text NOT NULL,
  PRIMARY KEY (org_id, workspace_id)
);

CREATE TABLE harness.workspace_manifests (
  org_id text NOT NULL,
  workspace_id text COLLATE "C" NOT NULL,
  revision bigint NOT NULL CHECK (revision >= 1),
  doc jsonb NOT NULL,
  region text NOT NULL,
  PRIMARY KEY (org_id, workspace_id, revision)
);

-- Session keys (D33): `wrapped` is null once shredded.
CREATE TABLE harness.workspace_keys (
  org_id text NOT NULL,
  session_id text COLLATE "C" NOT NULL,
  doc jsonb NOT NULL,
  region text NOT NULL,
  PRIMARY KEY (org_id, session_id)
);

-- P6.4: one workspace's write lease.
CREATE TABLE harness.workspace_writers (
  org_id text NOT NULL,
  workspace_id text COLLATE "C" NOT NULL,
  session_id text COLLATE "C" NOT NULL,
  run_id text COLLATE "C" NOT NULL,
  expires_at bigint NOT NULL,
  updated_at bigint NOT NULL,
  region text NOT NULL,
  PRIMARY KEY (org_id, workspace_id)
);
CREATE INDEX workspace_writers_by_session ON harness.workspace_writers (org_id, session_id);

-- Governed memories (P7.3): a proposal's id is its idempotency key.
CREATE TABLE harness.memories (
  org_id text NOT NULL,
  memory_id text COLLATE "C" NOT NULL,
  doc jsonb NOT NULL,
  owner_id text COLLATE "C" GENERATED ALWAYS AS (doc->>'ownerId') STORED,
  agent_key text COLLATE "C" GENERATED ALWAYS AS (doc->>'agentKey') STORED,
  session_id text COLLATE "C" GENERATED ALWAYS AS (doc->>'sessionId') STORED,
  run_id text COLLATE "C" GENERATED ALWAYS AS (doc->>'runId') STORED,
  scope text COLLATE "C" GENERATED ALWAYS AS (doc->>'scope') STORED,
  status text COLLATE "C" GENERATED ALWAYS AS (doc->>'status') STORED,
  superseded_by text COLLATE "C" GENERATED ALWAYS AS (doc->>'supersededBy') STORED,
  created_at bigint GENERATED ALWAYS AS ((doc->>'createdAt')::bigint) STORED,
  region text NOT NULL,
  PRIMARY KEY (org_id, memory_id)
);
CREATE INDEX memories_by_owner ON harness.memories (org_id, owner_id, created_at DESC);
CREATE INDEX memories_by_agent ON harness.memories (org_id, agent_key, created_at DESC);
CREATE INDEX memories_by_session ON harness.memories (org_id, session_id);
CREATE INDEX memories_by_run ON harness.memories (org_id, run_id);
CREATE INDEX memories_pending ON harness.memories (org_id, created_at) WHERE status = 'pending';
CREATE INDEX memories_by_superseded ON harness.memories (org_id, superseded_by) WHERE superseded_by IS NOT NULL;

-- Schedules (P8a). A cron slot is claimed by moving `next_fire_at` from the value the firer read (a guard in the
-- UPDATE) and inserting the firing under its unique slot key, in one unit.
CREATE TABLE harness.schedules (
  org_id text NOT NULL,
  id text COLLATE "C" NOT NULL,
  doc jsonb NOT NULL,
  actor_id text COLLATE "C" GENERATED ALWAYS AS (doc->>'actorId') STORED,
  agent_key text COLLATE "C" GENERATED ALWAYS AS (doc->>'agentKey') STORED,
  status text COLLATE "C" GENERATED ALWAYS AS (doc->>'status') STORED,
  next_fire_at bigint GENERATED ALWAYS AS ((doc->>'nextFireAt')::bigint) STORED,
  trigger_kind text COLLATE "C" GENERATED ALWAYS AS (doc->'trigger'->>'kind') STORED,
  connector_id text COLLATE "C" GENERATED ALWAYS AS (doc->'trigger'->>'connectorId') STORED,
  created_at bigint GENERATED ALWAYS AS ((doc->>'createdAt')::bigint) STORED,
  region text NOT NULL,
  PRIMARY KEY (org_id, id)
);
CREATE INDEX schedules_by_actor ON harness.schedules (org_id, actor_id, created_at DESC);
CREATE INDEX schedules_by_org ON harness.schedules (org_id, created_at DESC);
CREATE INDEX schedules_due ON harness.schedules (org_id, next_fire_at) WHERE status = 'active';
CREATE INDEX schedules_by_trigger ON harness.schedules (org_id, trigger_kind) WHERE status = 'active';

CREATE TABLE harness.schedule_firings (
  org_id text NOT NULL,
  id text COLLATE "C" NOT NULL,
  doc jsonb NOT NULL,
  schedule_id text COLLATE "C" GENERATED ALWAYS AS (doc->>'scheduleId') STORED,
  slot_key text COLLATE "C" GENERATED ALWAYS AS (doc->>'slotKey') STORED,
  slot_at bigint GENERATED ALWAYS AS ((doc->>'slotAt')::bigint) STORED,
  status text COLLATE "C" GENERATED ALWAYS AS (doc->>'status') STORED,
  attempts bigint GENERATED ALWAYS AS ((doc->>'attempts')::bigint) STORED,
  lease_until bigint GENERATED ALWAYS AS ((doc->>'leaseUntil')::bigint) STORED,
  watch_until bigint GENERATED ALWAYS AS ((doc->>'watchUntil')::bigint) STORED,
  watch_checked_at bigint GENERATED ALWAYS AS ((doc->>'watchCheckedAt')::bigint) STORED,
  region text NOT NULL,
  PRIMARY KEY (org_id, id),
  UNIQUE (org_id, schedule_id, slot_key)
);
CREATE INDEX schedule_firings_by_schedule ON harness.schedule_firings (org_id, schedule_id, slot_at DESC);
CREATE INDEX schedule_firings_pending ON harness.schedule_firings (org_id, lease_until) WHERE status = 'pending';
CREATE INDEX schedule_firings_watched ON harness.schedule_firings (org_id, watch_checked_at) WHERE status = 'admitted';

-- The schedules an org and an actor hold (H2, `org:<orgId>`, `actor:<orgId>:<actorId>`; a create locks the org's row
-- first, so two creates count in turn) and a webhook's per-window deliveries (`hits:...`, gone after `expires_at`).
CREATE TABLE harness.schedule_counters (
  org_id text NOT NULL,
  key text COLLATE "C" NOT NULL,
  count bigint NOT NULL CHECK (count >= 0),
  updated_at bigint NOT NULL,
  expires_at bigint,
  region text NOT NULL,
  PRIMARY KEY (org_id, key)
);
CREATE INDEX schedule_counters_expiry ON harness.schedule_counters (org_id, expires_at) WHERE expires_at IS NOT NULL;

SELECT kernel.apply_rls('harness.engine_signins', 'org', p_jobs => true);
SELECT kernel.apply_rls('harness.personal_choices', 'org', p_jobs => true);
SELECT kernel.apply_rls('harness.workspace_heads', 'org', p_jobs => true);
SELECT kernel.apply_rls('harness.workspace_manifests', 'org', p_jobs => true);
SELECT kernel.apply_rls('harness.workspace_keys', 'org', p_jobs => true);
SELECT kernel.apply_rls('harness.workspace_writers', 'org', p_jobs => true);
SELECT kernel.apply_rls('harness.memories', 'org', p_jobs => true);
SELECT kernel.apply_rls('harness.schedules', 'org', p_jobs => true);
SELECT kernel.apply_rls('harness.schedule_firings', 'org', p_jobs => true);
SELECT kernel.apply_rls('harness.schedule_counters', 'org', p_jobs => true);
GRANT SELECT, INSERT, UPDATE, DELETE ON harness.engine_signins, harness.personal_choices, harness.workspace_heads,
  harness.workspace_manifests, harness.workspace_keys, harness.workspace_writers, harness.memories, harness.schedules,
  harness.schedule_firings, harness.schedule_counters TO pipeshub_node, pipeshub_jobs;
