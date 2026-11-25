-- G2 (ADR-INBOX-01): a person's Inbox settings, one row each: muted workflows (oldest first), the pause, and the
-- channels beyond the Inbox their finished-run notices go to.
CREATE TABLE harness.inbox_preferences (
  org_id text NOT NULL,
  user_id text COLLATE "C" NOT NULL,
  muted_schedules text[] NOT NULL DEFAULT '{}',
  paused_until bigint,
  paused_until_resumed boolean NOT NULL DEFAULT false,
  run_notice_email boolean NOT NULL DEFAULT false,
  run_notice_slack boolean NOT NULL DEFAULT false,
  updated_at bigint NOT NULL,
  region text NOT NULL,
  PRIMARY KEY (org_id, user_id)
);

-- That a run's finished notice was decided: the first insert wins, whichever replica saw the run end.
CREATE TABLE harness.run_end_notices (
  org_id text NOT NULL,
  run_id text COLLATE "C" NOT NULL,
  user_id text COLLATE "C" NOT NULL,
  created_at bigint NOT NULL,
  region text NOT NULL,
  PRIMARY KEY (org_id, run_id)
);
CREATE INDEX run_end_notices_by_user ON harness.run_end_notices (org_id, user_id);
CREATE INDEX run_end_notices_by_age ON harness.run_end_notices (org_id, created_at);

-- One open stream of a person's on a conversation, renewed while it is open; a run that ends while one is live was
-- watched, so no notice is written for it.
CREATE TABLE harness.run_watchers (
  org_id text NOT NULL,
  watcher_id text COLLATE "C" NOT NULL,
  user_id text COLLATE "C" NOT NULL,
  session_id text COLLATE "C" NOT NULL,
  until bigint NOT NULL,
  region text NOT NULL,
  PRIMARY KEY (org_id, watcher_id)
);
CREATE INDEX run_watchers_by_person ON harness.run_watchers (org_id, user_id, session_id, until);

SELECT kernel.apply_rls('harness.inbox_preferences', 'org', p_jobs => true);
SELECT kernel.apply_rls('harness.run_end_notices', 'org', p_jobs => true);
SELECT kernel.apply_rls('harness.run_watchers', 'org', p_jobs => true);
GRANT SELECT, INSERT, UPDATE, DELETE ON harness.inbox_preferences, harness.run_end_notices, harness.run_watchers
  TO pipeshub_node, pipeshub_jobs;
