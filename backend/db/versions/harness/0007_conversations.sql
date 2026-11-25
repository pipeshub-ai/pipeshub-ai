-- "Continue privately" requests (`agentSessionForks`, SS3): one row per (org, source, actor), id = forkRecordId(key).
-- The fork's session id is chosen on the first ask; `result` is set once, by the fork's transaction (null until then).
-- The conversations themselves are the conversation branch's tables (WP109-FU-2), reached through its repository.
CREATE TABLE harness.session_forks (
  org_id text NOT NULL,
  id text COLLATE "C" NOT NULL,
  source_session_id text COLLATE "C" NOT NULL,
  actor_id text COLLATE "C" NOT NULL,
  fork_session_id text COLLATE "C" NOT NULL,
  result jsonb,
  created_at bigint NOT NULL,
  completed_at bigint,
  region text NOT NULL,
  PRIMARY KEY (org_id, id)
);
CREATE INDEX session_forks_by_source ON harness.session_forks (org_id, source_session_id);
CREATE INDEX session_forks_by_fork ON harness.session_forks (org_id, fork_session_id);
-- CLEANUP-G4: requests left uncommitted, oldest first.
CREATE INDEX session_forks_uncommitted ON harness.session_forks (org_id, created_at) WHERE result IS NULL;

SELECT kernel.apply_rls('harness.session_forks', 'org', p_jobs => true);
GRANT SELECT, INSERT, UPDATE, DELETE ON harness.session_forks TO pipeshub_node, pipeshub_jobs;
