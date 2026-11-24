-- The Node API's short-lived cache when no Redis is deployed (REDIS_MODE=postgres): sign-in sessions, the SAML desktop hand-off and
-- the e-mail rate-limit counters, shared by every API process and kept across a restart, as they are in Redis. Kept apart from
-- platform.kv_entries so that configuration listings never see a session key. Internal like the other runtime tables: deployment-wide,
-- no org column (a session names its org inside its value). A row past expires_at is dead: readers filter on it and writers replace it.
CREATE SCHEMA IF NOT EXISTS platform;

CREATE TABLE platform.cache_entries (
  key        text COLLATE "C" NOT NULL PRIMARY KEY,
  value      text NOT NULL,                   -- JSON, as the Redis cache stores it
  expires_at timestamptz,                     -- NULL: no expiry (a counter incremented without one, as INCR alone)
  updated_at timestamptz NOT NULL DEFAULT now());
CREATE INDEX cache_entries_expiry ON platform.cache_entries (expires_at) WHERE expires_at IS NOT NULL;

ALTER TABLE platform.cache_entries SET (autovacuum_vacuum_scale_factor = 0, autovacuum_vacuum_threshold = 2000);

SELECT kernel.register_internal('platform.cache_entries');

-- only the Node API holds sessions
GRANT SELECT, INSERT, UPDATE, DELETE ON platform.cache_entries TO pipeshub_node;
