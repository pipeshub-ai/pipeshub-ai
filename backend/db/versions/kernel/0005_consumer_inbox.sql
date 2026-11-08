-- Replay protection: a consumer inserts the message id in the same transaction as the effect. The key leads with
-- org_id, so one org can never learn that another org processed an id (a message-id-only key would be a cross-org
-- existence oracle). Rows older than the replay window are pruned by the jobs role. Do not partition by received_at:
-- a time column in the key would stop the same message being detected twice.
CREATE SCHEMA IF NOT EXISTS platform;
CREATE TABLE platform.consumer_inbox (
  org_id      text NOT NULL,
  message_id  text NOT NULL,
  received_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (org_id, message_id));
CREATE INDEX consumer_inbox_received_at ON platform.consumer_inbox (received_at);
SELECT kernel.apply_rls('platform.consumer_inbox', 'org', p_jobs => true);
GRANT USAGE ON SCHEMA platform TO pipeshub_jobs;
GRANT SELECT, INSERT ON platform.consumer_inbox TO pipeshub_node, pipeshub_connectors, pipeshub_indexing, pipeshub_query;
GRANT SELECT, DELETE ON platform.consumer_inbox TO pipeshub_jobs;
