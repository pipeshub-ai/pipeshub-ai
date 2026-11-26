-- A thread has one conversation per person (email) and bot, in the install's organisation.
CREATE TABLE integrations.slack_threads (
  org_id text NOT NULL,
  thread_id text NOT NULL,
  bot_id text NOT NULL,
  email text NOT NULL,
  conversation_id text NOT NULL,
  version int NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL,
  updated_at timestamptz NOT NULL,
  PRIMARY KEY (org_id, thread_id, bot_id, email)
);

SELECT kernel.apply_rls('integrations.slack_threads', 'org');
GRANT USAGE ON SCHEMA integrations TO pipeshub_node;
GRANT SELECT, INSERT, UPDATE, DELETE ON integrations.slack_threads TO pipeshub_node;
