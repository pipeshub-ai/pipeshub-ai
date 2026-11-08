-- The runtime's own state when no Redis is deployed: the key-value store (config, credentials, short-lived flags) and a work queue
-- with consumer groups. Both are deployment-wide plumbing, not organisation data, so they are internal tables (no org_id). The org of a
-- queued event travels inside its signed envelope, exactly as it does on a Redis stream.
CREATE SCHEMA IF NOT EXISTS platform;

-- Key-value entries. A row past expires_at is dead: readers filter on it and writers replace it.
CREATE TABLE platform.kv_entries (
  key        text COLLATE "C" NOT NULL PRIMARY KEY,
  value      bytea NOT NULL,
  expires_at timestamptz,
  updated_at timestamptz NOT NULL DEFAULT now());
CREATE INDEX kv_entries_expiry ON platform.kv_entries (expires_at) WHERE expires_at IS NOT NULL;

-- Queue messages. A message is deleted once no group still has a delivery row for it.
CREATE TABLE platform.queue_messages (
  id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  topic      text NOT NULL,
  msg_key    text,
  value      text NOT NULL,
  headers    text,
  created_at timestamptz NOT NULL DEFAULT now());
CREATE INDEX queue_messages_topic ON platform.queue_messages (topic, id);

-- A consumer group on a topic (the Streams consumer group).
-- An ephemeral group belongs to one process (a broadcast consumer): it starts at the tail and is dropped when seen_at, which its
-- consumer refreshes, goes stale.
CREATE TABLE platform.queue_groups (
  topic      text NOT NULL,
  group_id   text NOT NULL,
  ephemeral  boolean NOT NULL DEFAULT false,
  seen_at    timestamptz NOT NULL DEFAULT now(),
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (topic, group_id));

-- One delivery per message and group. ready: waiting (visible_at is the retry backoff); claimed: handed to a consumer until
-- locked_until (the Streams pending list); dead: given up after max attempts, kept for inspection. Ack deletes the row.
CREATE TABLE platform.queue_deliveries (
  group_id     text NOT NULL,
  topic        text NOT NULL,
  message_id   bigint NOT NULL,
  status       text NOT NULL DEFAULT 'ready',
  msg_key      text,                    -- the message's key, copied so per-key order is one index probe
  attempts     int NOT NULL DEFAULT 0,
  visible_at   timestamptz NOT NULL DEFAULT now(),
  locked_until timestamptz,
  consumer     text,
  last_error   text,
  PRIMARY KEY (group_id, topic, message_id));
-- the claim scans this in message order, past rows that other claimers hold
CREATE INDEX queue_deliveries_open ON platform.queue_deliveries (group_id, topic, message_id) WHERE status IN ('ready', 'claimed');
CREATE INDEX queue_deliveries_by_message ON platform.queue_deliveries (message_id);
-- per-key order: "is another delivery of this key open?" is a probe of this index, not a scan of the messages. The key leads, and the
-- probe tests the group as (group_id || '') = ..., which the primary key (group_id leads) cannot serve, nor can queue_deliveries_open
-- (same lead): on a table nothing has analysed yet the planner would otherwise walk the backlog through one of them (PG 18 does).
CREATE INDEX queue_deliveries_key ON platform.queue_deliveries (msg_key, group_id, topic, message_id) WHERE msg_key IS NOT NULL;

ALTER TABLE platform.queue_deliveries SET (fillfactor = 85, autovacuum_vacuum_scale_factor = 0, autovacuum_vacuum_threshold = 2000);
ALTER TABLE platform.queue_messages SET (autovacuum_vacuum_scale_factor = 0, autovacuum_vacuum_threshold = 2000);

SELECT kernel.register_internal('platform.kv_entries');
SELECT kernel.register_internal('platform.queue_messages');
SELECT kernel.register_internal('platform.queue_groups');
SELECT kernel.register_internal('platform.queue_deliveries');

GRANT USAGE ON SCHEMA platform TO pipeshub_node, pipeshub_connectors, pipeshub_indexing, pipeshub_query;
GRANT SELECT, INSERT, UPDATE, DELETE ON platform.kv_entries, platform.queue_messages, platform.queue_groups, platform.queue_deliveries
  TO pipeshub_node, pipeshub_connectors, pipeshub_indexing, pipeshub_query;
-- the narrow role of the processes that only read their configuration: this table and nothing else
GRANT USAGE ON SCHEMA platform TO pipeshub_kv;
GRANT SELECT, INSERT, UPDATE, DELETE ON platform.kv_entries TO pipeshub_kv;
