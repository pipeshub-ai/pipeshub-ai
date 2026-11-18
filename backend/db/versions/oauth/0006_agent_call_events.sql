-- One row per call made with a bearer token (PAT, OAuth access token, delegated or service token). Successful GETs are rolled up:
-- one row per token, method, route and UTC minute (`bucket`), with `count`. No request or response content is stored. Kept 30 days
-- (the retention sweep in the TTL sweeper, as for delegation events); the key order serves "newest first" for an org.
CREATE TABLE oauth.agent_call_events (
  org_id text NOT NULL,
  id oauth.oid24 NOT NULL,
  user_id oauth.oid24 NOT NULL,
  token_id text NOT NULL CHECK (char_length(token_id) <= 128),
  token_kind text NOT NULL CHECK (token_kind IN ('pat', 'oauth', 'delegated')),
  oauth_client_id text CHECK (char_length(oauth_client_id) <= 200),
  user_agent text CHECK (char_length(user_agent) <= 80),
  channel text CHECK (char_length(channel) <= 40),
  method text NOT NULL CHECK (char_length(method) <= 10),
  route text NOT NULL CHECK (char_length(route) <= 300),
  mcp_tool text CHECK (char_length(mcp_tool) <= 200),
  status_code int NOT NULL,
  duration_ms int NOT NULL,
  scope_denied boolean NOT NULL DEFAULT false,
  request_id text CHECK (char_length(request_id) <= 64),
  count int NOT NULL DEFAULT 1,
  bucket timestamptz,
  created_at timestamptz NOT NULL,
  PRIMARY KEY (org_id, id)
);
CREATE INDEX agent_call_events_by_time ON oauth.agent_call_events (org_id, created_at DESC, id DESC);
CREATE INDEX agent_call_events_by_token ON oauth.agent_call_events (org_id, token_id, created_at DESC);
CREATE INDEX agent_call_events_by_created ON oauth.agent_call_events (created_at);   -- the retention sweep reads across orgs by age
CREATE UNIQUE INDEX agent_call_events_rollup ON oauth.agent_call_events (org_id, token_id, method, route, bucket) WHERE bucket IS NOT NULL;

SELECT kernel.apply_rls('oauth.agent_call_events', 'org', p_jobs => true);   -- the sweeper removes rows past their retention

GRANT SELECT, INSERT, UPDATE ON oauth.agent_call_events TO pipeshub_node;
GRANT SELECT, DELETE ON oauth.agent_call_events TO pipeshub_jobs;
